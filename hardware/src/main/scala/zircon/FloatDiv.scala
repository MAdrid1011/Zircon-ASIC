package zircon

import chisel3._
import chisel3.util._

class FpDiv(f: Format,s: Spec) extends IterativeModule(f.width,s) {
  val radixBits = if (s.variant.startsWith("radix16")) 4 else if (s.variant.startsWith("radix4")) 2 else 1
  val fractionalBits = s.iterations*radixBits
  val qw = fractionalBits+4
  val k = qw+4
  val aa = Reg(new Decoded(f)); val bb = Reg(new Decoded(f)); val meta = Reg(new Meta(f))
  val rem = Reg(SInt((f.p+6).W)); val q = Reg(SInt(qw.W)); val d = Reg(SInt((f.p+3).W))
  val qp = Reg(UInt(qw.W)); val qn = Reg(UInt(qw.W))
  val tripleD = Reg(SInt((f.p+4).W))
  val negativeD = Reg(SInt((f.p+3).W)); val negativeTripleD = Reg(SInt((f.p+4).W))
  val exp = Reg(SInt(f.ew.W)); val sign = Reg(Bool())
  val mag = Reg(new Magnitude(f,k)); val out = Reg(new Response(f.width)); io.out.bits := out
  when(io.in.fire) {
    val a = FloatLogic.decode(io.in.bits.a,f); val b = FloatLogic.decode(io.in.bits.b,f)
    aa := a; bb := b; meta := FloatLogic.special(a,b,b,io.in.bits,f,"div")
    exp := a.exp-b.exp-fractionalBits.S; sign := a.sign ^ b.sign
  }
  when(phase === 1.U && !cancel) {
    d := bb.sig.zext
    val triple = Adders.signedAdd(bb.sig.zext,bb.sig.zext << 1,f.p+4)
    tripleD := triple
    negativeD := -bb.sig.zext
    negativeTripleD := Adders.signedAdd(0.S,triple,f.p+4,true)
    qn := 0.U
    if (radixBits == 1) {
      q := (aa.sig >= bb.sig).asUInt.zext
      rem := aa.sig.zext-bb.sig.zext
    } else {
      val twice = aa.sig << 1
      val seed = Mux(!aa.sig.orR,0.U,Mux(twice.zext >= triple,2.U,1.U))
      q := seed.zext
      qp := seed
      val minusOne = Adders.signedAdd(aa.sig.zext,bb.sig.zext,f.p+6,true)
      val minusTwo = Adders.signedAdd(aa.sig.zext,bb.sig.zext << 1,f.p+6,true)
      rem := Mux(seed === 2.U,minusTwo,Mux(seed === 1.U,minusOne,0.S))
    }
  }
  def srt(r: SInt,positive: UInt,negative: UInt): (SInt,UInt,UInt) = {
    val x = r << 2; val twice = x << 1
    val triple = tripleD
    val digit = Mux(twice >= triple,2.S,Mux(twice >= d,1.S,Mux(twice <= negativeTripleD,(-2).S,Mux(twice <= negativeD,(-1).S,0.S))))
    // Speculative remainder candidates run in parallel with quotient selection.
    val minusOne = Adders.signedAdd(x,d,f.p+6,true)
    val minusTwo = Adders.signedAdd(x,d << 1,f.p+6,true)
    val plusOne = Adders.signedAdd(x,d,f.p+6)
    val plusTwo = Adders.signedAdd(x,d << 1,f.p+6)
    val nr = Wire(SInt((f.p+6).W)); val np = Wire(UInt(qw.W)); val nn = Wire(UInt(qw.W))
    nr := Mux(digit === 2.S,minusTwo,Mux(digit === 1.S,minusOne,
      Mux(digit === (-1).S,plusOne,Mux(digit === (-2).S,plusTwo,x))))
    np := Cat(positive(qw-3,0),Mux(digit >= 0.S,digit.asUInt(1,0),0.U(2.W)))
    nn := Cat(negative(qw-3,0),Mux(digit < 0.S,(-digit).asUInt(1,0),0.U(2.W)))
    (nr,np,nn)
  }
  for(i <- 0 until s.iterations) when(phase === (i+2).U && !cancel) {
    if (radixBits == 1) {
      val next = Mux(rem >= 0.S,(rem << 1)-d,(rem << 1)+d)
      rem := next; q := (q << 1)+(next >= 0.S).asUInt.zext
    } else {
      val (r1,p1,n1) = srt(rem,qp,qn)
      val (rn,pn,nn) = if (radixBits == 4) srt(r1,p1,n1) else (r1,p1,n1)
      rem := rn; qp := pn; qn := nn
    }
  }
  when(phase === s.phases.indexOf("correct").U && !cancel) {
    val sumWidth = 1 << log2Ceil(qw)
    val correctedQ = if (radixBits == 1) q.asUInt else
      Adders.brentKung(qp.pad(sumWidth),(~qn).pad(sumWidth),sumWidth,!(rem < 0.S))(qw-1,0)
    val remainderNonzero = Mux(rem < 0.S,rem =/= negativeD,rem =/= 0.S)
    mag.mag := (correctedQ << 1) | remainderNonzero.asUInt
    mag.exp := exp-1.S; mag.sign := sign; mag.meta := meta
  }
  if(s.phases.contains("grs")) {
    val normalized = Reg(new Normalized(f,k)); val prepared = Reg(new Prepared(f))
    when(phase === s.phases.indexOf("round_normalize").U && !cancel) { normalized := FloatLogic.normalize(mag,f,k) }
    when(phase === s.phases.indexOf("grs").U && !cancel) { prepared := FloatLogic.prepare(normalized,f,k) }
    when(phase === (s.latency-1).U && !cancel) { out := FloatLogic.finish(prepared,f) }
  } else when(phase === (s.latency-1).U && !cancel) { out := FloatLogic.round(mag,f,k) }
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
