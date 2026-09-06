package zircon

import chisel3._
import chisel3.util._

class Request(w: Int) extends Bundle {
  val a = UInt(w.W)
  val b = UInt(w.W)
  val c = UInt(w.W)
  val rounding = UInt(3.W)
  val tag = UInt(32.W)
}
class Response(w: Int) extends Bundle {
  val bits = UInt(w.W)
  val flags = UInt(5.W)
  val tag = UInt(32.W)
  val remainder = UInt(w.W)
}
class UnitIO(width: Int, latency: Int) extends Bundle {
  val in = Flipped(Decoupled(new Request(width)))
  val out = Decoupled(new Response(width))
  val flush = Input(Bool())
  val stageValid = Output(UInt(latency.W))
  val occupancy = Output(UInt(log2Ceil(latency+1).W))
  val phase = Output(UInt(log2Ceil(latency+1).W))
  val iteration = Output(UInt(log2Ceil(latency+1).W))
}

abstract class ArithmeticModule(width: Int, val spec: Spec) extends Module {
  val io = IO(new UnitIO(width, spec.latency))
  val cancel = reset.asBool || io.flush
  val wasBlocked = RegNext(io.out.valid && !io.out.ready && !cancel, false.B)
  val previous = RegEnable(io.out.bits.asUInt, io.out.valid && !io.out.ready)
  when (wasBlocked && !cancel) {
    assert(io.out.valid, "output valid dropped during backpressure")
    assert(io.out.bits.asUInt === previous, "output payload changed during backpressure")
  }
  when (io.in.fire) { assert(io.in.bits.rounding <= 4.U, "invalid rounding mode") }
}

abstract class ElasticModule(width: Int, spec: Spec) extends ArithmeticModule(width, spec) {
  require(spec.kind == "elastic")
  val valid = RegInit(VecInit(Seq.fill(spec.latency)(false.B)))
  val advance = Wire(Vec(spec.latency, Bool()))
  for (i <- (0 until spec.latency).reverse) {
    advance(i) := !valid(i) || (if (i == spec.latency-1) io.out.ready else advance(i+1))
    when (cancel) { valid(i) := false.B }
      .elsewhen(advance(i)) { valid(i) := (if (i == 0) io.in.valid else valid(i-1)) }
  }
  io.in.ready := advance(0) && !cancel
  io.out.valid := valid.last && !cancel
  io.stageValid := valid.asUInt
  io.occupancy := PopCount(valid)
  io.phase := 0.U
  io.iteration := 0.U
  def stage[T <: Data](next: T, index: Int): T = {
    val reg = Reg(chiselTypeOf(next))
    val upstream = if (index == 0) io.in.valid else valid(index-1)
    when (!cancel && advance(index) && upstream) { reg := next }
    reg
  }
  def result(bits: UInt, flags: UInt, tag: UInt, remainder: UInt = 0.U): Response = {
    val r = Wire(new Response(width))
    r.bits := bits; r.flags := flags; r.tag := tag; r.remainder := remainder
    r
  }
}

abstract class IterativeModule(width: Int, spec: Spec) extends ArithmeticModule(width, spec) {
  require(spec.kind == "iterative")
  val phase = RegInit(0.U(log2Ceil(spec.latency+1).W))
  io.out.valid := phase === spec.latency.U && !cancel
  io.in.ready := (phase === 0.U || (io.out.valid && io.out.ready)) && !cancel
  when (cancel) { phase := 0.U }
    .elsewhen(io.in.fire) { phase := 1.U }
    .elsewhen(io.out.fire) { phase := 0.U }
    .elsewhen(phase =/= 0.U && phase < spec.latency.U) { phase := phase + 1.U }
  io.stageValid := Mux(phase === 0.U, 0.U, 1.U(spec.latency.W) << (phase-1.U))
  io.occupancy := (phase =/= 0.U).asUInt
  io.phase := phase
  io.iteration := VecInit((0 to spec.latency).map(i => spec.phases.take(i).count(_ == "iterate").U))(phase)
}
