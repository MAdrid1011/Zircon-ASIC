package zircon

import chisel3._
import chisel3.util._

class Decoded(f: Format) extends Bundle {
  val sign = Bool()
  val sig = UInt(f.p.W)
  val exp = SInt(12.W) // exponent of the least-significant significand bit
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
  val exp = SInt(12.W)
  val sign = Bool()
  val meta = new Meta(f)
}
class Aligned(f: Format, k: Int) extends Bundle {
  val a = UInt(k.W)
  val b = UInt(k.W)
  val sa = Bool()
  val sb = Bool()
  val exp = SInt(12.W)
  val meta = new Meta(f)
}

class Normalized(f: Format,k: Int) extends Bundle {
  val raw = new Magnitude(f,k)
  val top = SInt(12.W); val quantum = SInt(12.W); val cut = SInt(12.W)
}
class Prepared(f: Format) extends Bundle {
  val window = UInt((f.p+3).W)
  val top = SInt(12.W); val quantum = SInt(12.W)
  val sign = Bool(); val zero = Bool(); val meta = new Meta(f)
}

abstract class FloatingElasticModule(val format: Format,s: Spec) extends ElasticModule(format.width,s) {
  def pipedRound(x: Magnitude,k: Int,start: Int): Response = {
    if(format.name == "fp32") {
      val norm = stage(FloatLogic.normalize(x,format,k),start)
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
    d.exp := Mux(ef === 0.U,(f.emin-f.fb).S(12.W),ef.zext-(f.bias+f.fb).S) - shift.zext
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
  def normalize(x: Magnitude,f: Format,k: Int): Normalized = {
    val n = Wire(new Normalized(f,k)); n.raw := x
    val topBit = (k-1).U - PriorityEncoder(Reverse(x.mag))
    n.top := x.exp + topBit.zext
    n.quantum := Mux(n.top < f.emin.S,f.emin.S,n.top)-f.fb.S
    n.cut := n.quantum-x.exp
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
  def finish(p: Prepared,f: Format): Response = {
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
    val rounded = q +& inc
    val carry = rounded(f.p)
    val sig = Mux(carry,rounded >> 1,rounded)
    val re = p.quantum+f.fb.S+carry.asUInt.zext
    val tiny = !sig(f.fb)
    val ef = Mux(tiny,0.U,(re+f.bias.S).asUInt)
    val finiteOverflow = if (f.encoding == "ieee") false.B else {
      val maxsig = ((1 << f.p)-1-(if (f.encoding == "finite_nan") 1 else 0)).U(f.p.W)
      p.top > f.emax.S || (p.top === f.emax.S && (q > maxsig || (q === maxsig && inexact)))
    }
    val overflow = re > f.emax.S || finiteOverflow
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
  val a = if(f.name == "fp32") stage(da,0) else da
  val b = if(f.name == "fp32") stage(db,0) else db
  val meta = if(f.name == "fp32") stage(dm,0) else dm
  val aligned = FloatLogic.align(a.sig,a.exp,a.sign,b.sig,b.exp,b.sign,meta,f,k)
  if (f.name == "fp32") {
    // Near cancellation (opposite signs, exponent distance <= 1) uses only
    // fixed shifts; the far path retains the saturating alignment barrel.
    val diff = a.exp-b.exp
    val near = a.sign =/= b.sign && diff >= (-1).S && diff <= 1.S && !a.zero && !b.zero
    val nearA = Mux(diff >= 0.S,a.sig << 6,a.sig << 5)
    val nearB = Mux(diff <= 0.S,b.sig << 6,b.sig << 5)
    aligned.a := Mux(near,nearA,FloatLogic.alignOne(a.sig,a.exp,aligned.exp,k))
    aligned.b := Mux(near,nearB,FloatLogic.alignOne(b.sig,b.exp,aligned.exp,k))
  }
  if (s.latency == 1) io.out.bits := stage(FloatLogic.round(FloatLogic.add(aligned,f,k),f,k),0)
  else if(f.name == "fp32") {
    val first = stage(aligned,1)
    val summed = stage(FloatLogic.add(first,f,k),2)
    io.out.bits := pipedRound(summed,k,3)
  } else {
    val first = stage(aligned,0)
    io.out.bits := stage(FloatLogic.round(FloatLogic.add(first,f,k),f,k),s.latency-1)
  }
}

class FpMul(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  val da = FloatLogic.decode(io.in.bits.a,f); val db = FloatLogic.decode(io.in.bits.b,f)
  val dm = FloatLogic.special(da,db,db,io.in.bits,f,"mul")
  val a = if(f.name == "fp32") stage(da,0) else da
  val b = if(f.name == "fp32") stage(db,0) else db
  val meta = if(f.name == "fp32") stage(dm,0) else dm
  val k = 2*f.p+6
  var rows = if (f.p >= 16) Compressors.booth(a.sig,b.sig,f.p,false) else Compressors.baugh(a.sig,b.sig,f.p,false)
  var exp = a.exp+b.exp; var sign = a.sign ^ b.sign; var m = meta
  val targets = Compressors.targets(rows.size)
  val cuts = if(f.name == "fp32") 2 else s.latency-1
  val offset = if(f.name == "fp32") 1 else 0
  for (i <- 0 until cuts) {
    for (t <- targets.slice(targets.size*i/cuts,targets.size*(i+1)/cuts)) rows = Compressors.reduce(rows,2*f.p,t)
    rows = stage(VecInit(rows),i+offset).toSeq
    exp = stage(exp,i+offset); sign = stage(sign,i+offset); m = stage(m,i+offset)
  }
  val mag = Wire(new Magnitude(f,k))
  mag.mag := rows.reduce(_ + _); mag.exp := exp; mag.sign := sign; mag.meta := m
  io.out.bits := (if(f.name == "fp32") pipedRound(stage(mag,3),k,4) else pipedRound(mag,k,s.latency-1))
}

class FpFma(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  val da = FloatLogic.decode(io.in.bits.a,f); val db = FloatLogic.decode(io.in.bits.b,f); val dc = FloatLogic.decode(io.in.bits.c,f)
  val dm = FloatLogic.special(da,db,dc,io.in.bits,f,"fma")
  val deep = f.name == "fp32"
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
  val product = saved.reduce(_ + _)
  // The OR of unsigned carry-save rows detects zero without a carry chain.
  val productNonzero = saved.map(_.orR).reduce(_ || _)
  val aligned = FloatLogic.align(if(s.latency == 2) product else saved.reduce(_ | _),exp,sign,cc.sig,cc.exp,cc.sign,mm,f,k)
  if (s.latency == 2) io.out.bits := stage(FloatLogic.round(FloatLogic.add(aligned,f,k),f,k),1)
  else {
    val delta = exp-aligned.exp
    val productExact = delta >= 0.S
    // When C exceeds the product by > p+6 bits, one sticky product bit is
    // sufficient for every rounding mode; this also removes the far carry sum.
    val x = Mux(productExact,FloatLogic.alignOne(saved(0),exp,aligned.exp,k),0.U)
    val y = Mux(productExact,FloatLogic.alignOne(saved(1),exp,aligned.exp,k),productNonzero.asUInt)
    val opposite = sign =/= cc.sign
    val z = Mux(opposite,~aligned.b,aligned.b)
    var fused = Seq(x,y,z,Cat(0.U((k-1).W),opposite))
    for(t <- Compressors.targets(fused.size)) fused = Compressors.reduce(fused,k,t)
    val pair = stage(VecInit(fused),fusedStage)
    val sum = (pair(0)+pair(1)).asSInt
    val mag = Wire(new Magnitude(f,k))
    val ps = stage(sign,fusedStage)
    val zeroSign = stage(Mux(!productNonzero && cc.zero && sign === cc.sign,sign,mm.rounding === 2.U),fusedStage)
    mag.mag := Mux(sum < 0.S,(-sum).asUInt,sum.asUInt)
    mag.sign := Mux(sum === 0.S,zeroSign,ps ^ (sum < 0.S))
    mag.exp := stage(aligned.exp,fusedStage); mag.meta := stage(mm,fusedStage)
    val beforeRound = if(deep) stage(mag,4) else if(s.latency == 4) stage(mag,2) else mag
    io.out.bits := pipedRound(beforeRound,k,if(deep) 5 else s.latency-1)
  }
}
