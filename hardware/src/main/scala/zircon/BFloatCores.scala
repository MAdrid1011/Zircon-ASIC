package zircon

import chisel3._
import circt.stage.ChiselStage
import java.nio.file.{Files,Path}

/** Exhaustive verification top for the three exact unsigned significand cores. */
class BFloatCores extends Module {
  val io=IO(new Bundle {
    val a=Input(UInt(8.W)); val b=Input(UInt(8.W))
    val direct=Output(UInt(16.W)); val booth=Output(UInt(16.W)); val native=Output(UInt(16.W))
  })
  def sum(initial:Seq[UInt]):UInt={
    var rows=initial
    for(t<-Compressors.targets(rows.size)) rows=Compressors.reduce(rows,16,t)
    rows.reduce(_ + _)
  }
  io.direct:=sum(Compressors.baugh(io.a,io.b,8,false))
  io.booth:=sum(Compressors.booth(io.a,io.b,8,false))
  io.native:=io.a*io.b
}

object GenerateBFloatCores {
  def main(args:Array[String]):Unit={
    val out=Path.of(args(0)).toAbsolutePath;Files.createDirectories(out)
    Files.writeString(out.resolve("Unit.sv"),ChiselStage.emitSystemVerilog(new BFloatCores,
      firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable")))
  }
}
