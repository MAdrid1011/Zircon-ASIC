package zircon

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage
import java.nio.file.{Files,Path}

/** Drain before changing mode: 0 load input, 1 read/multiply/write, 2 read output. */
class SPMExample(bf16:Boolean=false) extends Module {
  val width=if(bf16)16 else 32
  val io=IO(new Bundle {
    val in=Vec(1,Flipped(Decoupled(new MemoryRequest(width))))
    val out=Vec(1,Decoupled(new MemoryResponse(width)))
    val mode=Input(UInt(2.W));val flush=Input(Bool())
    val flight=Output(Vec(1,Bool()));val queueCount=Output(Vec(1,UInt(4.W)));val outstanding=Output(Vec(1,UInt(4.W)))
  })
  val c=SPMConfig(256,width,1,1)
  val a=Module(new SPM(c));val b=Module(new SPM(c))
  val mul=Module(Arithmetic(if(bf16)"bf16" else "int32",if(bf16)"fma" else "mul",true,Contract.bundled()))
  a.io.flush:=io.flush;b.io.flush:=io.flush;mul.io.flush:=io.flush
  a.io.in(0).bits:=io.in(0).bits;a.io.in(0).valid:=io.in(0).valid && io.mode=/=2.U
  mul.io.in.valid:=a.io.out(0).valid && io.mode===1.U
  mul.io.in.bits.a:=a.io.out(0).bits.bits
  mul.io.in.bits.b:=(if(bf16)0x4000 else 3).U;mul.io.in.bits.c:=(if(bf16)0x3f80 else 0).U
  mul.io.in.bits.rounding:=0.U;mul.io.in.bits.tag:=a.io.out(0).bits.tag
  a.io.out(0).ready:=Mux(io.mode===1.U,mul.io.in.ready,io.out(0).ready)
  b.io.in(0).valid:=Mux(io.mode===2.U,io.in(0).valid,mul.io.out.valid && io.mode===1.U)
  b.io.in(0).bits:=io.in(0).bits
  when(io.mode===1.U) {
    b.io.in(0).bits.address:=mul.io.out.bits.tag;b.io.in(0).bits.write:=true.B
    b.io.in(0).bits.data:=mul.io.out.bits.bits;b.io.in(0).bits.mask:=((1<<(width/8))-1).U;b.io.in(0).bits.tag:=mul.io.out.bits.tag
  }
  mul.io.out.ready:=b.io.in(0).ready && io.mode===1.U
  b.io.out(0).ready:=io.out(0).ready
  io.in(0).ready:=Mux(io.mode===2.U,b.io.in(0).ready,a.io.in(0).ready)
  io.out(0).valid:=Mux(io.mode===0.U,a.io.out(0).valid,b.io.out(0).valid)
  io.out(0).bits:=Mux(io.mode===0.U,a.io.out(0).bits,b.io.out(0).bits)
  io.flight(0):=a.io.flight(0)||b.io.flight(0)||mul.io.stageValid.orR
  io.queueCount(0):=a.io.queueCount(0)+&b.io.queueCount(0)
  io.outstanding(0):=a.io.outstanding(0)+&b.io.outstanding(0)+&mul.io.occupancy
}

object GenerateSPMExample {
  def main(args:Array[String]):Unit={
    val out=Path.of(args(0)).toAbsolutePath;Files.createDirectories(out)
    Files.writeString(out.resolve("Unit.sv"),ChiselStage.emitSystemVerilog(new SPMExample(args.drop(1).contains("bf16")),
      firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays")))
  }
}
