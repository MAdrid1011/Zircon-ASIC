package zircon

import chisel3._
import chisel3.util._

class Decoded(f: Format) extends Bundle {
  val sign = Bool()
  val sig = UInt(f.p.W)
  val exp = SInt(f.ew.W) // exponent of the least-significant significand bit
  val zero = Bool()
  val inf = Bool()
  val nan = Bool()
  val snan = Bool()
}
class Meta(f: Format) extends Bundle {
  val special = Bool()
  val bits = UInt(f.width.W)
  val flags = UInt(5.W)
  val rounding = UInt(3.W)
  val tag = UInt(32.W)
}
class Magnitude(f: Format, k: Int) extends Bundle {
  val mag = UInt(k.W)
  val exp = SInt(f.ew.W)
  val sign = Bool()
  val meta = new Meta(f)
}
class Aligned(f: Format, k: Int) extends Bundle {
  val a = UInt(k.W)
  val b = UInt(k.W)
  val sa = Bool()
  val sb = Bool()
  val exp = SInt(f.ew.W)
  val meta = new Meta(f)
}

class Normalized(f: Format,k: Int) extends Bundle {
  val raw = new Magnitude(f,k)
  val top = SInt(f.ew.W); val quantum = SInt(f.ew.W); val cut = SInt(f.ew.W)
}
class Prepared(f: Format) extends Bundle {
  val window = UInt((f.p+3).W)
  val top = SInt(f.ew.W); val quantum = SInt(f.ew.W)
  val sign = Bool(); val zero = Bool(); val meta = new Meta(f)
}

abstract class FloatingElasticModule(val format: Format,s: Spec) extends ElasticModule(format.width,s) {
  def pipedRound(x: Magnitude,k: Int,start: Int): Response = {
    if(spec.phases.contains("grs")) {
      val norm = stage(FloatLogic.normalize(x,format,k,format.name == "e4m3fn" && spec.op == "fma",format.name == "fp32" && spec.op == "fma"),start)
      val prepared = stage(FloatLogic.prepare(norm,format,k),start+1)
      stage(FloatLogic.finish(prepared,format),start+2)
    } else stage(FloatLogic.round(x,format,k),start)
  }
}

object FloatLogic {
  def decode(bits: UInt,f: Format): Decoded = {
    val d = Wire(new Decoded(f))
    val ef = bits(f.width-2,f.fb); val frac = bits(f.fb-1,0)
    val raw = Cat(ef.orR,frac)
    val shift = PriorityEncoder(Reverse(raw))
    d.sign := bits(f.width-1)
    d.sig := raw << shift
    d.exp := Mux(ef === 0.U,(f.emin-f.fb).S(f.ew.W),ef.zext-(f.bias+f.fb).S) - shift.zext
    d.zero := raw === 0.U
    d.inf := (if (f.encoding == "ieee") ef.andR && !frac.orR else false.B)
    d.nan := (if (f.encoding == "ieee") ef.andR && frac.orR else if (f.encoding == "finite_nan") ef.andR && frac.andR else false.B)
    d.snan := (if (f.encoding == "ieee") d.nan && !frac(f.fb-1) else false.B)
    d
  }
  def special(a: Decoded,b: Decoded,c: Decoded,req: Request,f: Format,op: String): Meta = {
    val m = Wire(new Meta(f)); m.rounding := req.rounding; m.tag := req.tag
    val productSign = a.sign ^ b.sign
    val nan = a.nan || b.nan || (if (op == "fma") c.nan else false.B)
    val invalidProduct = (a.inf && b.zero) || (b.inf && a.zero)
    val signaling = a.snan || b.snan || (if (op == "fma") c.snan else false.B)
    val invalid = signaling || (op match {
      case "add" => !nan && a.inf && b.inf && (a.sign =/= b.sign)
      case "mul" => invalidProduct
      case "fma" => invalidProduct || (!nan && (a.inf || b.inf) && c.inf && (productSign =/= c.sign))
      case "div" => !nan && ((a.zero && b.zero) || (a.inf && b.inf))
    })
    val divzero = if (op == "div") b.zero && !a.zero && !a.inf && !nan else false.B
    val inf = op match {
      case "add" | "mul" => a.inf || b.inf
      case "fma" => a.inf || b.inf || c.inf
      case "div" => a.inf || b.inf
    }
    val sign = op match {
      case "add" => Mux(a.inf,a.sign,b.sign)
      case "fma" => Mux(a.inf || b.inf,productSign,c.sign)
      case _ => productSign
    }
    val infinity = if (f.encoding == "ieee") f.infBits else f.maxBits
    val raw = if (op == "div") Mux(b.inf,0.U(f.width.W),infinity.U(f.width.W)) else infinity.U(f.width.W)
    m.special := nan || invalid || divzero || inf
    m.bits := Mux(nan || invalid,f.nanBits.U,raw | (sign.asUInt << (f.width-1)))
    m.flags := Mux(invalid,16.U,Mux(divzero,8.U,0.U))
    m
  }
  def rightJam(x: UInt, amount: UInt, k: Int): UInt = {
    val sh = Mux(amount >= k.U,k.U,amount)
    val shifted = x >> sh
    val mask = ~((~0.U(k.W)) << sh)(k-1,0)
    shifted | (x & mask).orR.asUInt
  }
  def alignOne(m: UInt,e: SInt,base: SInt,k: Int): UInt = {
    val delta = e-base
    val left = (m.pad(k) << Mux(delta >= k.S,k.U,delta.asUInt))(k-1,0)
    Mux(delta >= 0.S,left,rightJam(m.pad(k),(-delta).asUInt,k))
  }
  def align(a: UInt,ea: SInt,sa: Bool,b: UInt,eb: SInt,sb: Bool,meta: Meta,f: Format,k: Int): Aligned = {
    val o = Wire(new Aligned(f,k))
    val ta = ea + (a.getWidth-1).S; val tb = eb + (b.getWidth-1).S
    val top = Mux(!a.orR,tb,Mux(!b.orR,ta,Mux(ta >= tb,ta,tb)))
    val base = top-(k-3).S
    o.a := alignOne(a,ea,base,k); o.b := alignOne(b,eb,base,k)
    o.sa := sa; o.sb := sb; o.exp := base; o.meta := meta
    o
  }
  def add(x: Aligned,f: Format,k: Int): Magnitude = {
    val o = Wire(new Magnitude(f,k))
    val sub = x.sa =/= x.sb; val aLarge = x.a >= x.b
    o.mag := Mux(sub,Mux(aLarge,x.a-x.b,x.b-x.a),x.a+x.b)
    o.sign := Mux(o.mag === 0.U,Mux(!x.a.orR && !x.b.orR && x.sa === x.sb,x.sa,x.meta.rounding === 2.U),Mux(sub,Mux(aLarge,x.sa,x.sb),x.sa))
    o.exp := x.exp; o.meta := x.meta
    o
  }
  def normalize(x: Magnitude,f: Format,k: Int,parallelCut: Boolean = false,compactQuantum: Boolean = false): Normalized = {
    val n = Wire(new Normalized(f,k)); n.raw := x
    val topBit = (k-1).U - PriorityEncoder(Reverse(x.mag))
    n.top := Adders.signedAdd(x.exp,topBit.zext,f.ew)
    val normalCut = topBit.zext-f.fb.S
    if(parallelCut) {
      val tinyCut = Adders.signedAdd((f.emin-f.fb).S,x.exp,f.ew,true)
      val isTiny = normalCut < tinyCut
      n.quantum := Mux(isTiny,(f.emin-f.fb).S,Adders.signedAdd(x.exp,normalCut,f.ew))
      n.cut := Mux(isTiny,tinyCut,normalCut)
    } else {
      n.quantum := (if(compactQuantum) Adders.signedAdd(Mux(n.top < f.emin.S,f.emin.S,n.top),(-f.fb).S,f.ew)
        else Mux(n.top < f.emin.S,(f.emin-f.fb).S,Adders.signedAdd(x.exp,normalCut,f.ew)))
      // Cancellation of the exponent in the normal case avoids two serial adders.
      n.cut := Mux(n.top < f.emin.S,Adders.signedAdd((f.emin-f.fb).S,x.exp,f.ew,true),if(compactQuantum) topBit.zext-f.fb.S else normalCut)
    }
    n
  }
  def prepare(n: Normalized,f: Format,k: Int): Prepared = {
    val x = n.raw; val p = Wire(new Prepared(f))
    val window = Mux(n.cut >= 3.S,rightJam(x.mag,(n.cut-3.S).asUInt,k),
      (x.mag << Mux(3.S-n.cut >= k.S,k.U,(3.S-n.cut).asUInt))(k-1,0))
    p.window := window; p.top := n.top; p.quantum := n.quantum
    p.sign := x.sign; p.zero := !x.mag.orR; p.meta := x.meta
    p
  }
  def finish(p: Prepared,f: Format,parallelEncoding: Boolean = false): Response = {
    val o = Wire(new Response(f.width))
    val window = p.window; val q = window(f.p+2,3)
    val guard = window(2); val sticky = window(1,0).orR; val inexact = guard || sticky
    val rm = p.meta.rounding
    val nq = window(f.p+1,2); val ng = window(1); val ns = window(0)
    val ni = (rm === 0.U && ng && (ns || nq(0))) || (rm === 4.U && ng) ||
      (rm === 2.U && p.sign && (ng || ns)) || (rm === 3.U && !p.sign && (ng || ns))
    val normRounded = nq +& ni
    val tinyAfter = p.top < (f.emin-1).S || (p.top === (f.emin-1).S && !normRounded(f.p))
    val inc = (rm === 0.U && guard && (sticky || q(0))) || (rm === 4.U && guard) ||
      (rm === 2.U && p.sign && inexact) || (rm === 3.U && !p.sign && inexact)
    val rounded = if(parallelEncoding) {
      val width = 1 << log2Ceil(f.p+1)
      Adders.brentKung(q.pad(width),0.U(width.W),width,inc)(f.p,0)
    } else q +& inc
    val carry = rounded(f.p)
    val sig = Mux(carry,rounded >> 1,rounded)
    val re = p.quantum+f.fb.S+carry.asUInt.zext
    val tiny = !sig(f.fb)
    val ef = if(parallelEncoding) {
      val withoutCarry = Adders.signedAdd(p.quantum,(f.fb+f.bias).S,f.ew)
      val withCarry = Adders.signedAdd(p.quantum,(f.fb+f.bias+1).S,f.ew)
      Mux(tiny,0.U,Mux(carry,withCarry,withoutCarry).asUInt)
    } else Mux(tiny,0.U,(re+f.bias.S).asUInt)
    val finiteOverflow = if (f.encoding == "ieee") false.B else {
      val maxsig = ((1 << f.p)-1-(if (f.encoding == "finite_nan") 1 else 0)).U(f.p.W)
      p.top > f.emax.S || (p.top === f.emax.S && (q > maxsig || (q === maxsig && inexact)))
    }
    val overflow = (if(parallelEncoding)
      Mux(carry,p.quantum > (f.emax-f.fb-1).S,p.quantum > (f.emax-f.fb).S)
      else re > f.emax.S) || finiteOverflow
    val inf = rm === 0.U || rm === 4.U || (rm === 2.U && p.sign) || (rm === 3.U && !p.sign)
    val overflowBits = if (f.encoding == "ieee") Mux(inf,f.infBits.U,f.maxBits.U) else f.maxBits.U
    val normalBits = ((ef << f.fb) | sig(f.fb-1,0))(f.width-2,0)
    val magnitude = Mux(p.zero,0.U,Mux(overflow,overflowBits,normalBits))
    o.bits := Mux(p.meta.special,p.meta.bits,magnitude | (p.sign.asUInt << (f.width-1)))
    o.flags := Mux(p.meta.special,p.meta.flags,Mux(p.zero,0.U,Mux(overflow,5.U,Mux(inexact,Mux(tinyAfter,3.U,1.U),0.U))))
    o.tag := p.meta.tag; o.remainder := 0.U
    o
  }
  def round(x: Magnitude,f: Format,k: Int): Response = finish(prepare(normalize(x,f,k),f,k),f)

}

class FpAdd(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  val k = f.p+8
  val da = FloatLogic.decode(io.in.bits.a,f); val db = FloatLogic.decode(io.in.bits.b,f)
  val dm = FloatLogic.special(da,db,db,io.in.bits,f,"add")
  val a = if(s.phases.contains("decode")) stage(da,0) else da
  val b = if(s.phases.contains("decode")) stage(db,0) else db
  val meta = if(s.phases.contains("decode")) stage(dm,0) else dm
  val aligned = FloatLogic.align(a.sig,a.exp,a.sign,b.sig,b.exp,b.sign,meta,f,k)
  locally {
    // Near cancellation (opposite signs, exponent distance <= 1) uses only
    // fixed shifts; the far path retains the saturating alignment barrel.
    val diff = Adders.signedAdd(a.exp,b.exp,f.ew,true)
    val reverseDiff = Adders.signedAdd(b.exp,a.exp,f.ew,true)
    val aLarge = a.exp >= b.exp
    val distance = Mux(aLarge,diff,reverseDiff).asUInt
    val near = if(f.name == "fp32") a.sign =/= b.sign && diff >= (-1).S && diff <= 1.S && !a.zero && !b.zero else false.B
    val nearA = Mux(diff >= 0.S,a.sig << 6,a.sig << 5)
    val nearB = Mux(diff <= 0.S,b.sig << 6,b.sig << 5)
    val shiftedA = (a.sig << 6).pad(k); val shiftedB = (b.sig << 6).pad(k)
    val farA = Mux(aLarge,shiftedA,FloatLogic.rightJam(shiftedA,distance,k))
    val farB = Mux(aLarge,FloatLogic.rightJam(shiftedB,distance,k),shiftedB)
    aligned.a := Mux(near,nearA,farA)
    aligned.b := Mux(near,nearB,farB)
    aligned.exp := Adders.signedAdd(Mux(aLarge,a.exp,b.exp),(-6).S,f.ew)
  }
  if (s.latency == 1) io.out.bits := stage(FloatLogic.round(FloatLogic.add(aligned,f,k),f,k),0)
  else if(s.phases.contains("decode")) {
    val first = stage(aligned,1)
    val summed = stage(FloatLogic.add(first,f,k),2)
    io.out.bits := pipedRound(summed,k,3)
  } else if(s.phases.contains("grs")) {
    val first = stage(aligned,0)
    io.out.bits := pipedRound(FloatLogic.add(first,f,k),k,1)
  } else {
    val first = stage(aligned,0)
    io.out.bits := stage(FloatLogic.round(FloatLogic.add(first,f,k),f,k),s.latency-1)
  }
}

class FpMul(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  val da = FloatLogic.decode(io.in.bits.a,f); val db = FloatLogic.decode(io.in.bits.b,f)
  val dm = FloatLogic.special(da,db,db,io.in.bits,f,"mul")
  val a = if(s.phases.contains("decode")) stage(da,0) else da
  val b = if(s.phases.contains("decode")) stage(db,0) else db
  val meta = if(s.phases.contains("decode")) stage(dm,0) else dm
  val k = 2*f.p+6
  var rows = if (f.p >= 16) Compressors.booth(a.sig,b.sig,f.p,false) else Compressors.baugh(a.sig,b.sig,f.p,false)
  var exp = a.exp+b.exp; var sign = a.sign ^ b.sign; var m = meta
  val targets = Compressors.targets(rows.size)
  val cuts = if(s.phases.contains("decode")) 2 else if(s.phases.contains("grs")) 1 else s.latency-1
  val offset = if(s.phases.contains("decode")) 1 else 0
  for (i <- 0 until cuts) {
    for (t <- targets.slice(targets.size*i/cuts,targets.size*(i+1)/cuts)) rows = Compressors.reduce(rows,2*f.p,t)
    rows = stage(VecInit(rows),i+offset).toSeq
    exp = stage(exp,i+offset); sign = stage(sign,i+offset); m = stage(m,i+offset)
  }
  val mag = Wire(new Magnitude(f,k))
  mag.mag := rows.reduce(_ + _); mag.exp := exp; mag.sign := sign; mag.meta := m
  io.out.bits := (if(s.phases.contains("decode")) pipedRound(stage(mag,3),k,4)
    else pipedRound(mag,k,if(s.phases.contains("grs")) 1 else s.latency-1))
}

class FpFma(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  val da = FloatLogic.decode(io.in.bits.a,f); val db = FloatLogic.decode(io.in.bits.b,f); val dc = FloatLogic.decode(io.in.bits.c,f)
  val dm = FloatLogic.special(da,db,dc,io.in.bits,f,"fma")
  val deep = s.phases.contains("decode")
  val a = if(deep) stage(da,0) else da; val b = if(deep) stage(db,0) else db
  val c = if(deep) stage(dc,0) else dc; val meta = if(deep) stage(dm,0) else dm
  val k = 3*f.p+8
  // Unsigned product rows have no modular sign-correction carry, so the
  // addend can enter the compressor before any carry-propagating product sum.
  var rows = Compressors.baugh(a.sig,b.sig,f.p,false)
  val targets = Compressors.targets(rows.size)
  var re = a.exp+b.exp; var rs = a.sign ^ b.sign; var rc = c; var rm = meta
  if(deep) {
    for(t <- targets.take(targets.size/2)) rows = Compressors.reduce(rows,2*f.p,t)
    rows = stage(VecInit(rows),1).toSeq
    re = stage(re,1); rs = stage(rs,1); rc = stage(rc,1); rm = stage(rm,1)
    for(t <- targets.drop(targets.size/2)) rows = Compressors.reduce(rows,2*f.p,t)
  } else for(t <- targets) rows = Compressors.reduce(rows,2*f.p,t)
  val productStage = if(deep) 2 else 0
  val fusedStage = productStage+1
  val saved = stage(VecInit(rows),productStage)
  val exp = stage(re,productStage); val sign = stage(rs,productStage)
  val cc = stage(rc,productStage); val mm = stage(rm,productStage)
  val productTop = stage(Adders.signedAdd(re,(2*f.p-1).S,f.ew),productStage)
  val addendTop = stage(Adders.signedAdd(rc.exp,(f.p-1).S,f.ew),productStage)
  val product = saved.reduce(_ + _)
  // The OR of unsigned carry-save rows detects zero without a carry chain.
  val productNonzero = saved.map(_.orR).reduce(_ || _)
  val aligned = FloatLogic.align(if(s.latency == 2) product else saved.reduce(_ | _),exp,sign,cc.sig,cc.exp,cc.sign,mm,f,k)
  if (s.latency == 2) io.out.bits := stage(FloatLogic.round(FloatLogic.add(aligned,f,k),f,k),1)
  else {
    val cdiff = Adders.signedAdd(addendTop,productTop,f.ew,true)
    val pdiff = Adders.signedAdd(productTop,addendTop,f.ew,true)
    val cDominates = !productNonzero || (cc.sig.orR && cdiff >= 0.S)
    val productExact = !cDominates || cdiff <= (f.p+6).S
    val productShift = f.p+6; val addendShift = 2*f.p+6
    val px = (saved(0).pad(k) << productShift)(k-1,0)
    val py = (saved(1).pad(k) << productShift)(k-1,0)
    val ca = (cc.sig.pad(k) << addendShift)(k-1,0)
    val ps = Mux(cdiff.asUInt >= k.U,k.U,cdiff.asUInt)
    aligned.b := Mux(cDominates,ca,FloatLogic.rightJam(ca,pdiff.asUInt,k))
    aligned.exp := Adders.signedAdd(Mux(cDominates,addendTop,productTop),(-(k-3)).S,f.ew)
    // When C exceeds the product by > p+6 bits, one sticky product bit is
    // sufficient for every rounding mode; this also removes the far carry sum.
    val x = Mux(productExact,Mux(cDominates,px >> ps,px),0.U)
    val y = Mux(productExact,Mux(cDominates,py >> ps,py),productNonzero.asUInt)
    val opposite = sign =/= cc.sign
    val z = Mux(opposite,~aligned.b,aligned.b)
    var fused = Seq(x,y,z,Cat(0.U((k-1).W),opposite))
    for(t <- Compressors.targets(fused.size)) fused = Compressors.reduce(fused,k,t)
    val pair = stage(VecInit(fused),fusedStage)
    val sumWidth = 1 << log2Ceil(k)
    val sum = Adders.brentKung(pair(0).pad(sumWidth),pair(1).pad(sumWidth),sumWidth)(k-1,0).asSInt
    val mag = Wire(new Magnitude(f,k))
    val productSign = stage(sign,fusedStage)
    val zeroSign = stage(Mux(!productNonzero && cc.zero && sign === cc.sign,sign,mm.rounding === 2.U),fusedStage)
    // Small formats compute -(a+b) = ~a + ~b + 2 concurrently. Wide formats
    // retain the smaller serial absolute-value candidate after measured STA.
    val negativePair = if(f.width <= 8) Compressors.reduce(Seq(~pair(0),~pair(1),2.U(k.W)),k,2) else Seq.empty
    val negated = if(f.width > 8)
      Adders.brentKung((~sum.asUInt).pad(sumWidth),0.U(sumWidth.W),sumWidth,true.B)(k-1,0)
      else Adders.brentKung(negativePair(0).pad(sumWidth),negativePair(1).pad(sumWidth),sumWidth)(k-1,0)
    mag.mag := Mux(sum < 0.S,negated,sum.asUInt)
    mag.sign := Mux(sum === 0.S,zeroSign,productSign ^ (sum < 0.S))
    mag.exp := stage(aligned.exp,fusedStage); mag.meta := stage(mm,fusedStage)
    val beforeRound = if(deep) stage(mag,4) else if(s.latency == 4) stage(mag,2) else mag
    io.out.bits := pipedRound(beforeRound,k,if(deep) 5 else if(s.phases.contains("grs")) 2 else s.latency-1)
  }
}
