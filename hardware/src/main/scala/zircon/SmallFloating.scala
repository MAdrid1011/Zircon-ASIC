package zircon

import chisel3._
import chisel3.util._

/** E2M1 values are integer multiples of 1/2. No exponent alignment is needed. */
class Fp4Arithmetic(f: Format,s: Spec,op: String) extends ElasticModule(4,s) {
  require(f.name == "e2m1" && Set("add","mul","fma").contains(op))
  val values = Vector(0,1,2,3,4,6,8,12)
  def magnitude(bits: UInt): UInt = VecInit(values.map(_.U(4.W)))(bits(2,0))
  def signed(mag: UInt,sign: Bool): SInt = Mux(sign,-mag.zext,mag.zext)
  val a = magnitude(io.in.bits.a); val b = magnitude(io.in.bits.b); val c = magnitude(io.in.bits.c)
  val sa = io.in.bits.a(3); val sb = io.in.bits.b(3); val sc = io.in.bits.c(3)
  val productSign = sa ^ sb
  val product = signed(a*b,productSign)
  val request = io.in.bits
  val raw = Wire(SInt(10.W)); val zeroSign = Wire(Bool())
  val rm = if(op == "fma") stage(request.rounding,0) else request.rounding
  val tag = if(op == "fma") stage(request.tag,0) else request.tag
  op match {
    case "add" =>
      raw := signed(a,sa) +& signed(b,sb)
      zeroSign := Mux(!a.orR && !b.orR && sa === sb,sa,rm === 2.U)
    case "mul" =>
      raw := product
      zeroSign := productSign
    case "fma" =>
      raw := stage(product,0) +& stage(signed(c << 1,sc),0)
      zeroSign := stage(Mux((!a.orR || !b.orR) && !c.orR && productSign === sc,productSign,request.rounding === 2.U),0)
  }
  val sign = if(op == "mul") productSign else Mux(raw === 0.S,zeroSign,raw < 0.S)
  val mag = if(op == "mul") a*b else Mux(raw < 0.S,-raw,raw).asUInt
  val denominator = if(op == "add") 1 else 2
  val exact = VecInit(values.map(v => mag === (v*denominator).U)).asUInt.orR
  val lower = MuxCase(0.U(3.W),(1 to 7).reverse.map(i => (mag >= (values(i)*denominator).U) -> i.U(3.W)))
  val nearest = MuxCase(0.U(3.W),(0 until 7).reverse.map { i =>
    val midpoint = ((values(i)+values(i+1))*denominator).U
    val up = (mag << 1) > midpoint || ((mag << 1) === midpoint && (rm === 4.U || (i%2 == 1).B))
    up -> (i+1).U(3.W)
  })
  val away = (rm === 2.U && sign) || (rm === 3.U && !sign)
  val rounded = Mux(rm === 0.U || rm === 4.U,nearest,Mux(away && !exact,lower+1.U,lower))
  val overflow = mag > (12*denominator).U
  // IEEE-style tininess after precision rounding, before gradual underflow.
  val tiny = mag < (2*denominator).U && Mux(rm === 0.U || rm === 4.U,
    (mag << 2) < (7*denominator).U,Mux(away,(mag << 1) <= (3*denominator).U,true.B))
  val flags = Mux(overflow,5.U,Mux(exact,0.U,Mux(tiny,3.U,1.U)))
  io.out.bits := stage(result(Cat(sign,Mux(overflow,7.U(3.W),rounded)),flags,tag),s.latency-1)
}
