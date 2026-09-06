package zircon

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage
import java.nio.file.{Files, Path}

case class SPMConfig(capacityBytes: Int = 4096, dataWidth: Int = 32, banks: Int = 1, ports: Int = 1, ihp: Boolean = false) {
  require(Seq(8,16,32,64).contains(dataWidth))
  require(Seq(1,2,4,8,16).contains(banks) && ports >= 1 && ports <= 8)
  val bytes = dataWidth / 8
  require(capacityBytes > 0 && capacityBytes % (banks*bytes) == 0)
  val rows = capacityBytes / banks / bytes
  require(!ihp || (dataWidth == 32 && rows == 1024), "IHP backend requires 1024x32 banks")
  val pw = math.max(1,log2Ceil(ports))
  val bw = math.max(1,log2Ceil(banks))
}

class MemoryRequest(w: Int) extends Bundle {
  val address = UInt(32.W)
  val write = Bool()
  val data = UInt(w.W)
  val mask = UInt((w/8).W)
  val tag = UInt(32.W)
}
class MemoryResponse(w: Int) extends Bundle {
  val bits = UInt(w.W)
  val tag = UInt(32.W)
  val write = Bool()
  val status = UInt(2.W)
}

class IHPSRAM extends BlackBox {
  override def desiredName = "RM_IHPSG13_1P_1024x32_c2_bm_bist"
  val io = IO(new Bundle {
    val A_CLK = Input(Clock()); val A_MEN = Input(Bool()); val A_WEN = Input(Bool()); val A_REN = Input(Bool())
    val A_ADDR = Input(UInt(10.W)); val A_DIN = Input(UInt(32.W)); val A_DLY = Input(Bool())
    val A_DOUT = Output(UInt(32.W)); val A_BM = Input(UInt(32.W))
    val A_BIST_CLK = Input(Clock()); val A_BIST_EN = Input(Bool()); val A_BIST_MEN = Input(Bool())
    val A_BIST_WEN = Input(Bool()); val A_BIST_REN = Input(Bool()); val A_BIST_ADDR = Input(UInt(10.W))
    val A_BIST_DIN = Input(UInt(32.W)); val A_BIST_BM = Input(UInt(32.W))
  })
}

class SRAMBank(c: SPMConfig) extends Module {
  val io = IO(new Bundle {
    val enable = Input(Bool()); val write = Input(Bool())
    val address = Input(UInt(math.max(1,log2Ceil(c.rows)).W))
    val data = Input(UInt(c.dataWidth.W)); val mask = Input(UInt(c.bytes.W))
    val result = Output(UInt(c.dataWidth.W))
  })
  if(c.ihp) {
    val m = Module(new IHPSRAM)
    m.io.A_CLK := clock; m.io.A_MEN := io.enable
    m.io.A_WEN := io.enable && io.write; m.io.A_REN := io.enable && !io.write
    m.io.A_ADDR := io.address; m.io.A_DIN := io.data; m.io.A_DLY := true.B
    m.io.A_BM := Cat((0 until c.bytes).reverse.map(i => Fill(8,io.mask(i))))
    m.io.A_BIST_CLK := false.B.asClock; m.io.A_BIST_EN := false.B; m.io.A_BIST_MEN := false.B
    m.io.A_BIST_WEN := false.B; m.io.A_BIST_REN := false.B; m.io.A_BIST_ADDR := 0.U
    m.io.A_BIST_DIN := 0.U; m.io.A_BIST_BM := 0.U
    io.result := m.io.A_DOUT
  } else {
    val m = SyncReadMem(c.rows,Vec(c.bytes,UInt(8.W)))
    io.result := m.readWrite(io.address,io.data.asTypeOf(Vec(c.bytes,UInt(8.W))),io.mask.asBools,io.enable,io.write).asUInt
  }
}

class SPM(val c: SPMConfig = SPMConfig()) extends Module {
  val io = IO(new Bundle {
    val in = Vec(c.ports,Flipped(Decoupled(new MemoryRequest(c.dataWidth))))
    val out = Vec(c.ports,Decoupled(new MemoryResponse(c.dataWidth)))
    val flush = Input(Bool())
    val rr = Output(Vec(c.banks,UInt(c.pw.W)))
    val grantValid = Output(Vec(c.banks,Bool()))
    val grant = Output(Vec(c.banks,UInt(c.pw.W)))
    val flight = Output(Vec(c.ports,Bool()))
    val queueCount = Output(Vec(c.ports,UInt(2.W)))
    val outstanding = Output(Vec(c.ports,UInt(2.W)))
    val bankWrite = Output(Vec(c.banks,Bool()))
    val bankAddress = Output(Vec(c.banks,UInt(32.W)))
    val bankData = Output(Vec(c.banks,UInt(c.dataWidth.W)))
    val bankMask = Output(Vec(c.banks,UInt(c.bytes.W)))
  })
  val cancel = reset.asBool || io.flush
  val rr = RegInit(VecInit(Seq.fill(c.banks)(0.U(c.pw.W))))
  val flight = RegInit(VecInit(Seq.fill(c.ports)(false.B)))
  val meta = Reg(Vec(c.ports,new MemoryResponse(c.dataWidth)))
  val flightBank = Reg(Vec(c.ports,UInt(c.bw.W)))
  val queues = Reg(Vec(c.ports,Vec(2,new MemoryResponse(c.dataWidth))))
  val head = RegInit(VecInit(Seq.fill(c.ports)(false.B)))
  val count = RegInit(VecInit(Seq.fill(c.ports)(0.U(2.W))))
  val eligible = Wire(Vec(c.ports,Bool()))
  val target = Wire(Vec(c.ports,UInt(c.bw.W)))
  val status = Wire(Vec(c.ports,UInt(2.W)))
  val selected = Wire(Vec(c.banks,UInt(c.pw.W)))
  val found = Wire(Vec(c.banks,Bool()))
  val bankResult = Wire(Vec(c.banks,UInt(c.dataWidth.W)))

  for(p <- 0 until c.ports) {
    val r = io.in(p).bits
    val occupied = count(p) +& flight(p)
    io.out(p).valid := count(p) =/= 0.U && !cancel
    io.out(p).bits := queues(p)(head(p))
    eligible(p) := io.in(p).valid && !cancel && (occupied < 2.U || io.out(p).fire)
    status(p) := Mux(r.address > (c.capacityBytes-c.bytes).U,1.U,
      Mux((r.address & (c.bytes-1).U) =/= 0.U,2.U,0.U))
    target(p) := (r.address >> log2Ceil(c.bytes)) & (c.banks-1).U
    io.in(p).ready := eligible(p) && (status(p) =/= 0.U ||
      (0 until c.banks).map(b => found(b) && selected(b) === p.U).reduce(_ || _))
    io.flight(p) := flight(p); io.queueCount(p) := count(p); io.outstanding(p) := occupied
    val stalled = RegNext(io.out(p).valid && !io.out(p).ready && !cancel,false.B)
    val held = RegEnable(io.out(p).bits.asUInt,io.out(p).valid && !io.out(p).ready)
    when(stalled && !cancel) { assert(io.out(p).valid && io.out(p).bits.asUInt === held) }
    assert(occupied <= 2.U)
    when(cancel) { flight(p) := false.B; count(p) := 0.U; head(p) := false.B }
      .otherwise {
        flight(p) := io.in(p).fire
        when(io.in(p).fire) {
          meta(p).bits := 0.U; meta(p).tag := r.tag; meta(p).write := r.write; meta(p).status := status(p)
          flightBank(p) := target(p)
        }
        when(io.out(p).fire) { head(p) := !head(p) }
        when(flight(p)) {
          val tail = head(p) ^ count(p)(0)
          queues(p)(tail) := meta(p)
          queues(p)(tail).bits := Mux(!meta(p).write && meta(p).status === 0.U,bankResult(flightBank(p)),0.U)
          assert(count(p) < 2.U || io.out(p).fire)
        }
        count(p) := count(p) + flight(p).asUInt - io.out(p).fire.asUInt
      }
  }
  for(b <- 0 until c.banks) {
    val choices = Wire(Vec(c.ports,Bool()))
    val masked = Wire(Vec(c.ports,Bool()))
    for(p <- 0 until c.ports) {
      choices(p) := eligible(p) && status(p) === 0.U && target(p) === b.U
      masked(p) := choices(p) && p.U >= rr(b)
    }
    found(b) := choices.asUInt.orR
    val winner = PriorityEncoderOH(Mux(masked.asUInt.orR,masked.asUInt,choices.asUInt))
    selected(b) := OHToUInt(winner)
    // One-hot selection avoids decoding the grant again on the SRAM input path.
    val r = Mux1H((0 until c.ports).map(p => winner(p) -> io.in(p).bits))
    val m = Module(new SRAMBank(c))
    m.io.enable := found(b); m.io.write := r.write
    m.io.address := r.address >> (log2Ceil(c.bytes)+log2Ceil(c.banks))
    m.io.data := r.data; m.io.mask := r.mask
    bankResult(b) := m.io.result
    io.rr(b) := rr(b); io.grantValid(b) := found(b); io.grant(b) := selected(b)
    io.bankWrite(b) := found(b) && r.write; io.bankAddress(b) := r.address
    io.bankData(b) := r.data; io.bankMask(b) := r.mask
    when(cancel) { rr(b) := 0.U }
      .elsewhen(found(b)) { rr(b) := Mux(selected(b) === (c.ports-1).U,0.U,selected(b)+1.U) }
  }
}

/** Production boundary excludes observation-only pins from physical I/O budgets. */
class SPMPhysical(c: SPMConfig) extends Module {
  val io=IO(new Bundle {
    val in=Vec(c.ports,Flipped(Decoupled(new MemoryRequest(c.dataWidth))))
    val out=Vec(c.ports,Decoupled(new MemoryResponse(c.dataWidth)))
    val flush=Input(Bool())
  })
  val spm=Module(new SPM(c))
  spm.io.in <> io.in; io.out <> spm.io.out; spm.io.flush:=io.flush
}

object GenerateSPM {
  def main(args: Array[String]): Unit = {
    require(args.length >= 5,"capacity width banks ports output [ihp]")
    val c = SPMConfig(args(0).toInt,args(1).toInt,args(2).toInt,args(3).toInt,args.lift(5).contains("ihp"))
    val stream = getClass.getResourceAsStream("/zircon-spm.json")
    require(stream != null,"SPM contract resource missing")
    val contract = try ujson.read(new String(stream.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8)) finally stream.close()
    require(contract("latency").num == 2 && contract("credits_per_port").num == 2 && contract("response_depth").num == 2)
    val out = Path.of(args(4)).toAbsolutePath; Files.createDirectories(out)
    val physical=args.lift(6).contains("physical")
    val sv = ChiselStage.emitSystemVerilog(if(physical) new SPMPhysical(c) else new SPM(c),firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays"))
    Files.writeString(out.resolve("Unit.sv"),sv)
    val hash = java.security.MessageDigest.getInstance("SHA-256").digest(sv.getBytes(java.nio.charset.StandardCharsets.UTF_8)).map(b=>f"${b & 255}%02x").mkString
    Files.writeString(out.resolve("manifest.json"),ujson.write(ujson.Obj("capacity_bytes"->c.capacityBytes,"data_width"->c.dataWidth,
      "banks"->c.banks,"ports"->c.ports,"ihp"->c.ihp,"physical_boundary"->physical,"latency"->2,"rtl_sha256"->hash,"contract"->contract,"qualification"->"unqualified"),indent=2))
  }
}

object GenerateSRAMBank {
  def main(args: Array[String]): Unit = {
    val out=Path.of(args(0)).toAbsolutePath; Files.createDirectories(out)
    Files.writeString(out.resolve("Bank.sv"),ChiselStage.emitSystemVerilog(
      new SRAMBank(SPMConfig(ihp=true)),firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable")))
  }
}
