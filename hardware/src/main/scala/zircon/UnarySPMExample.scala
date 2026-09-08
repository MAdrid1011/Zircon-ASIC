package zircon

import chisel3._
import chisel3.util._
import _root_.circt.stage.ChiselStage
import java.nio.file.{Files,Path}

/** Mode 0 loads input SRAM, mode 1 transforms words, mode 2 reads output SRAM.
  * The caller drains the network before changing mode; tags carry addresses. */
class UnarySPMExample(name: String,operation: String,c: Contract = Contract.bundled()) extends Module {
  require(Set("exp","rsqrt").contains(operation))
  val f=c.format(name)
  val io=IO(new Bundle {
    val in=Vec(1,Flipped(Decoupled(new MemoryRequest(f.width))))
    val out=Vec(1,Decoupled(new MemoryResponse(f.width)))
    val mode=Input(UInt(2.W));val flush=Input(Bool())
    val flight=Output(Vec(1,Bool()))
    val queueCount=Output(Vec(1,UInt(16.W)));val outstanding=Output(Vec(1,UInt(16.W)))
  })
  val input=Module(new SPM(SPMConfig(256,f.width,1,1)))
  val output=Module(new SPM(SPMConfig(256,f.width,1,1)))
  val unary=Module(Arithmetic(name,operation,true,c))
  val multiply=if(operation=="rsqrt") Some(Module(Arithmetic(name,"mul",true,c))) else None
  input.io.flush:=io.flush;output.io.flush:=io.flush;unary.io.flush:=io.flush
  input.io.in(0).bits:=io.in(0).bits
  input.io.in(0).valid:=io.in(0).valid && io.mode=/=2.U
  unary.io.in.valid:=input.io.out(0).valid && io.mode===1.U
  unary.io.in.bits.a:=input.io.out(0).bits.bits
  unary.io.in.bits.b:=0.U;unary.io.in.bits.c:=0.U
  unary.io.in.bits.rounding:=0.U;unary.io.in.bits.tag:=input.io.out(0).bits.tag
  input.io.out(0).ready:=Mux(io.mode===1.U,unary.io.in.ready,io.out(0).ready)
  multiply.foreach { m =>
    m.io.flush:=io.flush
    m.io.in.valid:=unary.io.out.valid
    m.io.in.bits.a:=unary.io.out.bits.bits
    m.io.in.bits.b:=((f.bias+1)<<f.fb).U;m.io.in.bits.c:=0.U
    m.io.in.bits.rounding:=0.U;m.io.in.bits.tag:=unary.io.out.bits.tag
    unary.io.out.ready:=m.io.in.ready
  }
  val last=multiply.map(_.io.out).getOrElse(unary.io.out)
  output.io.in(0).valid:=Mux(io.mode===2.U,io.in(0).valid,last.valid && io.mode===1.U)
  output.io.in(0).bits:=io.in(0).bits
  when(io.mode===1.U) {
    output.io.in(0).bits.address:=last.bits.tag;output.io.in(0).bits.write:=true.B
    output.io.in(0).bits.data:=last.bits.bits;output.io.in(0).bits.mask:=((1<<(f.width/8))-1).U
    output.io.in(0).bits.tag:=last.bits.tag
  }
  last.ready:=output.io.in(0).ready && io.mode===1.U
  output.io.out(0).ready:=io.out(0).ready
  io.in(0).ready:=Mux(io.mode===2.U,output.io.in(0).ready,input.io.in(0).ready)
  io.out(0).valid:=Mux(io.mode===0.U,input.io.out(0).valid,output.io.out(0).valid)
  io.out(0).bits:=Mux(io.mode===0.U,input.io.out(0).bits,output.io.out(0).bits)
  io.flight(0):=input.io.flight(0)||output.io.flight(0)||unary.io.stageValid.orR||multiply.map(_.io.stageValid.orR).getOrElse(false.B)
  io.queueCount(0):=input.io.queueCount(0)+&output.io.queueCount(0)
  io.outstanding(0):=input.io.outstanding(0)+&output.io.outstanding(0)+&unary.io.occupancy+&multiply.map(_.io.occupancy).getOrElse(0.U)
}

object GenerateUnarySPMExample {
  def main(args: Array[String]): Unit = {
    require(args.length==3,"GenerateUnarySPMExample FORMAT OP OUTPUT")
    val out=Path.of(args(2)).toAbsolutePath;Files.createDirectories(out)
    Files.writeString(out.resolve("Unit.sv"),ChiselStage.emitSystemVerilog(new UnarySPMExample(args(0),args(1)),
      firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays")))
  }
}
