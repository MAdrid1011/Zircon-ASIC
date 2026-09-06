package zircon

import chisel3._
import chisel3.util._

class FpDiv(f: Format,s: Spec) extends IterativeModule(f.width,s) {
  val radixBits = if (f.name == "fp32") 4 else if (f.name == "fp16") 2 else 1
  val fractionalBits = s.iterations*radixBits
  val qw = fractionalBits+4
  val k = qw+4
  val aa = Reg(new Decoded(f)); val bb = Reg(new Decoded(f)); val meta = Reg(new Meta(f))
  val rem = Reg(SInt((f.p+6).W)); val q = Reg(SInt(qw.W)); val d = Reg(SInt((f.p+3).W))
  val exp = Reg(SInt(12.W)); val sign = Reg(Bool())
  val mag = Reg(new Magnitude(f,k)); val out = Reg(new Response(f.width)); io.out.bits := out
  when(io.in.fire) {
    val a = FloatLogic.decode(io.in.bits.a,f); val b = FloatLogic.decode(io.in.bits.b,f)
    aa := a; bb := b; meta := FloatLogic.special(a,b,b,io.in.bits,f,"div")
    exp := a.exp-b.exp-fractionalBits.S; sign := a.sign ^ b.sign
  }
  when(phase === 1.U && !cancel) {
    d := bb.sig.zext
    if (radixBits == 1) {
      q := (aa.sig >= bb.sig).asUInt.zext
      rem := aa.sig.zext-bb.sig.zext
    } else {
      val twice = aa.sig << 1
      val seed = Mux(twice >= (bb.sig +& (bb.sig << 1)),2.U,Mux(twice >= bb.sig,1.U,0.U))
      q := seed.zext
      rem := aa.sig.zext-Mux(seed === 2.U,bb.sig << 1,Mux(seed === 1.U,bb.sig,0.U)).zext
    }
  }
  def srt(r: SInt,quotient: SInt): (SInt,SInt) = {
    val x = r << 2; val twice = x << 1
    val triple = d + (d << 1)
    val digit = Mux(twice >= triple,2.S,Mux(twice >= d,1.S,Mux(twice <= -triple,(-2).S,Mux(twice <= -d,(-1).S,0.S))))
    val mult = Mux(digit === 2.S,d << 1,Mux(digit === 1.S,d,Mux(digit === (-1).S,-d,Mux(digit === (-2).S,-(d << 1),0.S))))
    val nr = Wire(SInt((f.p+6).W)); val nq = Wire(SInt(qw.W))
    nr := x-mult; nq := (quotient << 2)+digit
    (nr,nq)
  }
  for(i <- 0 until s.iterations) when(phase === (i+2).U && !cancel) {
    if (radixBits == 1) {
      val next = Mux(rem >= 0.S,(rem << 1)-d,(rem << 1)+d)
      rem := next; q := (q << 1)+(next >= 0.S).asUInt.zext
    } else {
      val (r1,q1) = srt(rem,q)
      val (rn,qn) = if (radixBits == 4) srt(r1,q1) else (r1,q1)
      rem := rn; q := qn
    }
  }
  when(phase === (s.latency-2).U && !cancel) {
    val correctedQ = if (radixBits == 1) q else Mux(rem < 0.S,q-1.S,q)
    val correctedR = Mux(rem < 0.S,rem+d,rem)
    mag.mag := (correctedQ.asUInt << 1) | (correctedR =/= 0.S).asUInt
    mag.exp := exp-1.S; mag.sign := sign; mag.meta := meta
  }
  when(phase === (s.latency-1).U && !cancel) { out := FloatLogic.round(mag,f,k) }
}

class Fp4Div(f: Format,s: Spec) extends ElasticModule(f.width,s) {
  // Exact integer table in half-unit values, generated independently in Scala.
  val values = Vector(0,1,2,3,4,6,8,12)
  def entry(a: Int,b: Int,rm: Int): Int = {
    val sign = ((a ^ b) & 8) != 0
    val n = values(a & 7); val d = values(b & 7)
    if (d == 0) {
      if (n == 0) 16 << 4 else (8 << 4) | (if(sign)15 else 7)
    } else {
      val num = 2*n
      if (num > 12*d) (5 << 4) | (if(sign)15 else 7)
      else {
        val lower = values.lastIndexWhere(v => v*d <= num)
        val exact = values(lower)*d == num
        val upper = math.min(7,lower+1)
        val midpoint = num*2-(values(lower)+values(upper))*d
        val up = !exact && (rm match {
          case 0 => midpoint > 0 || (midpoint == 0 && (lower & 1) == 1)
          case 4 => midpoint >= 0
          case 2 => sign
          case 3 => !sign
          case _ => false
        })
        val r = if (up) upper else lower
        val tiny = num < 2*d && (rm match {
          case 0 | 4 => num*4 < 7*d
          case 2 if sign => num*2 <= 3*d
          case 3 if !sign => num*2 <= 3*d
          case _ => true
        })
        val flags = if (exact) 0 else if (tiny) 3 else 1
        (flags << 4) | (if (sign) 8 else 0) | r
      }
    }
  }
  val table = VecInit((0 until 2048).map { idx =>
    val rm = idx >> 8; val a = (idx >> 4) & 15; val b = idx & 15
    entry(a,b,if(rm <= 4)rm else 0).U(9.W)
  })
  val v = table(Cat(io.in.bits.rounding,io.in.bits.a,io.in.bits.b))
  io.out.bits := stage(result(v(3,0),v(8,4),io.in.bits.tag),0)
}
