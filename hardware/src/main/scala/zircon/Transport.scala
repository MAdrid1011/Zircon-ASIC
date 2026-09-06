package zircon

import chisel3._
import chisel3.util._

class DelayLine(w: Int,cycles: Int) extends ElasticModule(w,Spec("transport","delay",cycles,"elastic",Seq.fill(cycles)("transport"),"delay")) {
  var r = result(io.in.bits.a,io.in.bits.b(4,0),io.in.bits.tag,io.in.bits.c)
  for(i <- 0 until cycles) r = stage(r,i)
  io.out.bits := r
}

class StreamFIFO(w: Int,depth: Int) extends ArithmeticModule(w,Spec("transport","fifo",depth,"fifo",Seq.fill(depth)("entry"),"non_fall_through_fifo")) {
  require(depth > 0)
  val memory = Reg(Vec(depth,new Response(w)))
  val head = RegInit(0.U(math.max(1,log2Ceil(depth)).W))
  val tail = RegInit(0.U(math.max(1,log2Ceil(depth)).W))
  val count = RegInit(0.U(log2Ceil(depth+1).W))
  io.out.valid := count =/= 0.U && !cancel
  io.in.ready := (count < depth.U || io.out.fire) && !cancel
  io.out.bits := memory(head)
  io.occupancy := count
  io.stageValid := VecInit((0 until depth).map(i => count > i.U)).asUInt
  io.phase := 0.U; io.iteration := 0.U
  when(cancel) { head := 0.U; tail := 0.U; count := 0.U }
    .otherwise {
      when(io.in.fire) {
        memory(tail).bits := io.in.bits.a; memory(tail).flags := io.in.bits.b(4,0)
        memory(tail).remainder := io.in.bits.c; memory(tail).tag := io.in.bits.tag
        tail := Mux(tail === (depth-1).U,0.U,tail+1.U)
      }
      when(io.out.fire) { head := Mux(head === (depth-1).U,0.U,head+1.U) }
      when(io.in.fire =/= io.out.fire) { count := Mux(io.in.fire,count+1.U,count-1.U) }
    }
}

class NetworkExample(c: Contract) extends Module {
  val io = IO(new Bundle {
    val in = Flipped(Decoupled(new Request(8)))
    val out = Decoupled(new Response(8))
    val flush = Input(Bool())
    val stageValid = Output(UInt(8.W))
    val occupancy = Output(UInt(4.W))
    val phase = Output(UInt(1.W))
    val iteration = Output(UInt(1.W))
  })
  val a = Module(new IntAdd(8,true,c.spec("int8","add")))
  val fifo = Module(new StreamFIFO(8,3))
  val b = Module(new IntMul(8,true,c.spec("int8","mul")))
  val delay = Module(new DelayLine(8,2))
  a.io.in <> io.in
  def wire(from: ArithmeticModule,to: ArithmeticModule,transport: Boolean): Unit = {
    to.io.in.valid := from.io.out.valid; from.io.out.ready := to.io.in.ready
    to.io.in.bits.a := from.io.out.bits.bits
    to.io.in.bits.b := (if(transport) from.io.out.bits.flags else 3.U)
    to.io.in.bits.c := (if(transport) from.io.out.bits.remainder else 0.U)
    to.io.in.bits.tag := from.io.out.bits.tag; to.io.in.bits.rounding := 0.U
  }
  wire(a,fifo,true); wire(fifo,b,false); wire(b,delay,true)
  Seq(a,fifo,b,delay).foreach(_.io.flush := io.flush)
  io.out <> delay.io.out
  io.stageValid := Cat(delay.io.stageValid,b.io.stageValid,fifo.io.stageValid,a.io.stageValid)
  io.occupancy := a.io.occupancy +& fifo.io.occupancy +& b.io.occupancy +& delay.io.occupancy
  io.phase := 0.U; io.iteration := 0.U
}

object GenerateNetwork extends App {
  val c = new Contract(java.nio.file.Path.of("../src/zircon_asic/data/contract.json"))
  val out = java.nio.file.Path.of(args(0)); java.nio.file.Files.createDirectories(out)
  val sv = _root_.circt.stage.ChiselStage.emitSystemVerilog(new NetworkExample(c),firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays"))
  java.nio.file.Files.writeString(out.resolve("Unit.sv"),sv)
}
