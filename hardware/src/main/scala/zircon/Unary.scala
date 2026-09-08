package zircon

import chisel3._
import chisel3.util._

object SfuResources {
  lazy val json: ujson.Value = {
    val stream = getClass.getResourceAsStream("/zircon-sfu.json")
    require(stream != null, "shared unary coefficient resource is missing")
    try ujson.read(new String(stream.readAllBytes(), java.nio.charset.StandardCharsets.UTF_8))
    finally stream.close()
  }
  def config(f: Format,s: Spec): ujson.Value = s.unaryResources.getOrElse(
    json.obj.get("implementations").flatMap(_.obj.get(s"${f.name}.${s.op}")).map(_("configuration"))
      .getOrElse(json("formats")(f.name)))
  def integer(v: ujson.Value): BigInt = if(v.isInstanceOf[ujson.Str]) BigInt(v.str) else BigDecimal(v.num.toString).toBigIntExact.get
  def rom(values: ujson.Value, index: UInt, width: Int): UInt =
    VecInit(values.arr.map(v => integer(v).U(width.W)).toSeq)(index)
  def special(a: Decoded, req: Request, f: Format, op: String): Meta = {
    val m = Wire(new Meta(f)); m.rounding := req.rounding; m.tag := req.tag
    val sign = a.sign.asUInt << (f.width-1)
    val invalid = a.snan || (if(op == "sqrt" || op == "rsqrt") a.sign && !a.zero && !a.nan else false.B)
    m.special := a.nan || a.inf || a.zero || invalid
    m.flags := Mux(invalid,16.U,Mux(a.zero && (op == "rcp" || op == "rsqrt").B,8.U,0.U))
    val raw = op match {
      case "rcp" | "rsqrt" => Mux(a.zero,f.infBits.U,0.U) | (if(op == "rcp") sign else Mux(a.zero,sign,0.U))
      case "sqrt" => Mux(a.zero,sign,f.infBits.U)
    }
    m.bits := Mux(a.nan || invalid,f.nanBits.U,raw)
    m
  }
}

class UnaryRefinement(f: Format, fraction: Int, q: Int) extends Bundle {
  val a = new Decoded(f)
  val meta = new Meta(f)
  val y = UInt((fraction+2).W)
  val temporary = UInt((fraction+3).W)
  val companion = UInt((fraction+3).W)
  val candidate = UInt((q+2).W)
  val product = UInt((2*q+f.fb+4).W)
  val delta = UInt((2*q+f.fb+4).W)
}

abstract class UnaryElastic(f: Format,s: Spec) extends FloatingElasticModule(f,s) {
  if(SfuResources.config(f,s).obj.get("ready_topology").exists(_.str == "suffix_tree")) {
    // Parallel suffix products keep the downstream ready input at the final OR.
    // !all(valid[i:]) || out.ready is exactly the ordinary elastic recurrence.
    var suffix: Seq[Bool]=valid.toSeq
    for(level <- 0 until log2Ceil(s.latency)) {
      val previous=suffix;val distance=1 << level
      suffix=previous.indices.map(i => if(i+distance < s.latency) previous(i) && previous(i+distance) else previous(i))
    }
    for(i <- 0 until s.latency) advance(i):=io.out.ready || !suffix(i)
  }
  // A registered credit count has the same pre-edge value as PopCount(valid).
  // It keeps long pipelines' observation ports inside the ordinary IO budget.
  if(s.latency > 16) {
    val count=RegInit(0.U(log2Ceil(s.latency+1).W))
    when(cancel) { count:=0.U }
      .elsewhen(io.in.fire =/= io.out.fire) { count:=Mux(io.in.fire,count+1.U,count-1.U) }
    // A pipeline accepts exactly when it is not full, or its last stage is
    // delivered on this edge.  This is equivalent to advance(0), while the
    // registered count avoids making the input-ready port depend on every
    // downstream valid bit.
    io.in.ready := (io.out.ready || count =/= s.latency.U) && !cancel
    io.occupancy:=count
    assert(count===PopCount(valid),"unary occupancy differs from stage valid bits")
  }
  /** Exact rectangular product, optional fused addend, and matched sideband cuts.
    * The caller registers the final carry-propagate result in the last stage. */
  def multiply[T <: Data](a: UInt,b: UInt,sideband: T,index: Int,stages: Int,
                          addend: Option[UInt] = None): (UInt,T) = {
    require(stages >= 1)
    if(stages == 1) return (addend.map(x => a*b+x).getOrElse(a*b),sideband)
    if(b.getWidth > a.getWidth && b.litOption.isEmpty) return multiply(b,a,sideband,index,stages,addend)
    val width = a.getWidth+b.getWidth+addend.size
    val positions = b.litOption.map(n => (0 until b.getWidth).filter(n.testBit)).getOrElse(0 until b.getWidth)
    var heap: Seq[UInt] = positions.map(i => Mux(b(i),(a.pad(width) << i)(width-1,0),0.U(width.W)))
    heap = heap ++ addend.map(_.pad(width))
    if(heap.isEmpty) heap = Seq(0.U(width.W))
    val targets = Compressors.targets(heap.size)
    var payload = sideband
    val cuts = stages-1
    for(i <- 0 until cuts) {
      for(t <- targets.slice(targets.size*i/cuts,targets.size*(i+1)/cuts)) heap=Compressors.reduce(heap,width,t)
      heap=stage(VecInit(heap),index+i).toSeq
      payload=stage(payload,index+i)
    }
    val size=1 << log2Ceil(width)
    val sum=if(heap.size==1) heap.head else Adders.brentKung(heap.head.pad(size),heap(1).pad(size),size)(width-1,0)
    (sum,payload)
  }
}

/** Independent elastic unary lane. Every refinement owns spatial resources. */
abstract class FpAlgebraic(f: Format,s: Spec,op: String) extends UnaryElastic(f,s) {
  require(Set("fp32","fp16","bf16").contains(f.name))
  val cfg = SfuResources.config(f,s)
  val q = f.fb+4
  val multiplyStages = cfg.obj.get("multiply_stages").map(_.num.toInt).getOrElse(1)
  val a0 = FloatLogic.decode(io.in.bits.a,f)
  val meta0 = SfuResources.special(a0,io.in.bits,f,op)
  def exponent(a: Decoded): SInt = {
    val e = a.exp+f.fb.S
    (op match {
      case "rcp" => -e
      case "sqrt" => e >> 1
      case "rsqrt" => -(e >> 1)
    }) - (q+1).S
  }
  def magnitude(a: Decoded,meta: Meta,bits: UInt): Magnitude = {
    val m = Wire(new Magnitude(f,q+2)); m.mag := bits; m.exp := exponent(a)
    m.sign := (if(op == "rcp") a.sign else false.B); m.meta := meta; m
  }
  if(s.variant == "normalized_table") {
    val a = stage(a0,0); val meta = stage(meta0,0)
    val e = a.exp+f.fb.S
    val index = if(op == "rcp") a.sig(f.fb-1,0) else Cat(e.asUInt(0),a.sig(f.fb-1,0))
    val value = SfuResources.rom(cfg("tables")(op)("values"),index,q+2)
    val m = stage(magnitude(a,meta,value),1)
    io.out.bits := pipedRound(m,q+2,2)
  } else if(s.variant == "reciprocal_digits") {
    class DivideState extends Bundle {
      val a = new Decoded(f); val meta = new Meta(f)
      val remainder = UInt((f.p+1).W); val quotient = UInt((q+1).W)
      val triple = UInt((f.p+2).W)
    }
    val initial = Wire(new DivideState)
    initial.a := a0; initial.meta := meta0
    initial.remainder := (BigInt(1) << f.fb).U; initial.quotient := 0.U; initial.triple:=0.U
    var state = stage(initial,0)
    val step = cfg.obj.get("root_bits_per_stage").map(_.num.toInt).getOrElse(2)
    val lookahead=cfg.obj.get("division_lookahead").exists(_.bool)
    require(!lookahead || step==2)
    val offset=if(lookahead) 1 else 0
    if(lookahead) {
      val next=Wire(new DivideState);next:=state
      val size=1 << log2Ceil(f.p+2)
      next.triple:=Adders.brentKung(state.a.sig.pad(size),(state.a.sig << 1).pad(size),size)(f.p+1,0)
      state=stage(next,1)
    }
    val groups = (q+step)/step
    for(g <- 0 until groups) {
      var rem = state.remainder; var quotient = state.quotient
      val count=math.min(step,q+1-g*step)
      if(lookahead && count==2) {
        val width=f.p+2;val size=1 << log2Ceil(width)
        val twice=(rem << 1).pad(size)
        def difference(divisor: UInt): UInt =
          Adders.brentKung(twice,~divisor.pad(size),size,true.B)
        val d1=difference(state.a.sig);val d2=difference(state.a.sig << 1);val d3=difference(state.triple)
        val first=d2(size);val second=Mux(first,d3(size),d1(size))
        val nextRem=Wire(UInt((f.p+1).W))
        nextRem:=Mux(first,Mux(second,d3(width-1,0),d2(width-1,0)),Mux(second,d1(width-1,0),twice(width-1,0))) << 1
        val nextQuotient=Wire(UInt((q+1).W));nextQuotient:=Cat(quotient,first,second)
        rem=nextRem;quotient=nextQuotient
      } else for(i <- 0 until count) {
        val size=1 << log2Ceil(f.p+1)
        val diff=Adders.brentKung(rem.pad(size),~state.a.sig.pad(size),size,true.B)
        val ge = diff(size)
        val r = Wire(UInt((f.p+1).W)); r := Mux(ge,diff(f.p,0),rem) << 1
        val v = Wire(UInt((q+1).W)); v := Cat(quotient,ge)
        rem = r; quotient = v
      }
      val next = Wire(new DivideState); next := state; next.remainder := rem; next.quotient := quotient
      state = stage(next,g+1+offset)
    }
    val m = stage(magnitude(state.a,state.meta,Cat(state.quotient,state.remainder.orR)),groups+1+offset)
    io.out.bits := pipedRound(m,q+2,groups+2+offset)
    require(s.latency == groups+5+offset)
  } else if(op == "sqrt" && s.variant == "nonrestoring") {
    val a = stage(a0,0); val meta = stage(meta0,0)
    class RootState extends Bundle {
      val radicand = UInt((2*q+2).W)
      val remainder = SInt((q+4).W)
      val root = UInt((q+1).W)
      val a = new Decoded(f); val meta = new Meta(f)
    }
    val initial = Wire(new RootState)
    val parity = (a.exp+f.fb.S).asUInt(0)
    initial.radicand := (a.sig << parity) << (2*q-f.fb)
    initial.remainder := 0.S; initial.root := 0.U; initial.a := a; initial.meta := meta
    var state = stage(initial,1)
    val step = cfg.obj.get("root_bits_per_stage").map(_.num.toInt).getOrElse(2)
    val lookahead = cfg.obj.get("root_lookahead").exists(_.bool)
    require(!lookahead || step == 2)
    val groups = (q+step)/step
    for(g <- 0 until groups) {
      var r = state.remainder
      var root = state.root
      val bits=(q-g*step to math.max(0,q-(g+1)*step+1) by -1)
      if(lookahead && bits.size == 2) {
        val width=q+4;val size=1 << log2Ceil(width)
        def addRows(rows: Seq[UInt]): UInt = {
          var heap=rows.map(x => x.pad(width)(width-1,0))
          for(t <- Compressors.targets(heap.size)) heap=Compressors.reduce(heap,width,t)
          Adders.brentKung(heap.head.pad(size),heap(1).pad(size),size)(width-1,0)
        }
        val base=Cat(r.asUInt,state.radicand(2*bits.head+1,2*bits.last))(width-1,0)
        val q8=(root << 3)(width-1,0);val q16=(root << 4)(width-1,0)
        val positive0 = r >= 0.S
        val common=Mux(positive0,~q8,q8)
        val assumePositive=addRows(Seq(base,Mux(positive0,~q16,0.U(width.W)),common,
          Mux(positive0,(-7).S(width.W).asUInt,7.U(width.W))))
        val assumeNegative=addRows(Seq(base,Mux(positive0,0.U(width.W),q16),common,
          Mux(positive0,0.U(width.W),15.U(width.W))))
        val firstBase=Wire(SInt(size.W));firstBase:=Cat(r.asUInt,state.radicand(2*bits.head+1,2*bits.head)).asSInt
        val trial=Cat(root,Mux(positive0,1.U(2.W),3.U(2.W)))
        val first=Adders.brentKung(firstBase.asUInt,Mux(positive0,~trial.pad(size),trial.pad(size)),size,positive0)(width-1,0)
        val positive1 = !first(width-1)
        val second=Mux(positive1,assumePositive,assumeNegative)
        r=second.asSInt
        val nextRoot=Wire(UInt((q+1).W));nextRoot:=Cat(root,positive1,!second(width-1));root=nextRoot
      } else for(bit <- bits) {
        val pair = state.radicand(2*bit+1,2*bit)
        val trial = Cat(root,Mux(r >= 0.S,1.U(2.W),3.U(2.W)))
        val next = Wire(SInt((q+4).W))
        val size=1 << log2Ceil(q+4)
        val expanded=Wire(SInt(size.W));expanded:=Cat(r.asUInt,pair).asSInt
        val sub=r >= 0.S
        next := Adders.brentKung(expanded.asUInt,Mux(sub,~trial.pad(size),trial.pad(size)),size,sub)(q+3,0).asSInt
        val nextRoot = Wire(UInt((q+1).W)); nextRoot := Cat(root,(next >= 0.S))
        r = next; root = nextRoot
      }
      val next = Wire(new RootState); next := state; next.remainder := r; next.root := root
      state = stage(next,g+2)
    }
    val remainder = Mux(state.remainder < 0.S,state.remainder+Cat(state.root,1.U(1.W)).zext,state.remainder)
    val m = stage(magnitude(state.a,state.meta,Cat(state.root,remainder =/= 0.S)),groups+2)
    io.out.bits := pipedRound(m,q+2,groups+3)
    require(s.latency == groups+6)
  } else {
    val params = cfg("refinement")
    val fraction = params("fraction_bits").num.toInt
    val indexBits = params("table_bits").num.toInt
    val iterations = params("iterations").num.toInt
    val initial = Wire(new UnaryRefinement(f,fraction,q)); initial := 0.U.asTypeOf(initial)
    initial.a := a0; initial.meta := meta0
    var state = stage(initial,0)
    val seed = Wire(chiselTypeOf(initial)); seed := state
    val fracIndex = state.a.sig(f.fb-1,f.fb-indexBits)
    val seedIndex = if(op == "rcp") fracIndex else Cat((state.a.exp+f.fb.S).asUInt(0),fracIndex)
    seed.y := SfuResources.rom(params((if(op == "rcp") "rcp" else "rsqrt")+"_seed"),seedIndex,fraction+2)
    state = stage(seed,1)
    var cursor = 2
    val gold = s.variant == "goldschmidt"
    if(gold) {
      val m = if(op == "rcp") state.a.sig else state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
      val (p,sideband) = multiply(m,state.y,state,cursor,multiplyStages)
      val next = Wire(chiselTypeOf(initial)); next := sideband; next.companion := p >> f.fb
      state = stage(next,cursor+multiplyStages-1); cursor += multiplyStages
    }
    for(i <- 0 until iterations) {
      if(!gold && op != "rcp") {
        val (p,sideband) = multiply(state.y,state.y,state,cursor,multiplyStages)
        val square = Wire(chiselTypeOf(initial)); square := sideband; square.temporary := p >> fraction
        state = stage(square,cursor+multiplyStages-1); cursor += multiplyStages
      }
      val m = if(op == "rcp") state.a.sig else state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
      val preStages = if(gold && op == "rcp") 1 else multiplyStages
      val (p,sideband) = if(gold && op == "rcp") (state.companion,state)
        else if(gold) {val (v,t)=multiply(state.companion,state.y,state,cursor,multiplyStages);(v >> fraction,t)}
        else {val (v,t)=multiply(m,if(op == "rcp") state.y else state.temporary,state,cursor,multiplyStages);(v >> f.fb,t)}
      val mult = Wire(chiselTypeOf(initial)); mult := sideband; mult.temporary := p
      state = stage(mult,cursor+preStages-1); cursor += preStages
      val coefficient = if(cfg.obj.get("prefix_factor").exists(_.bool)) {
        val size=1 << log2Ceil(fraction+3)
        Adders.brentKung((BigInt(if(op == "rcp") 2 else 3) << fraction).U(size.W),
          ~state.temporary.pad(size),size,true.B)(fraction+2,0)
      } else ((BigInt(if(op == "rcp") 2 else 3) << fraction).U((fraction+3).W)-state.temporary)
      val (refined,refinedMeta)=multiply(state.y,coefficient,state,cursor,multiplyStages)
      val update = Wire(chiselTypeOf(initial)); update := refinedMeta
      update.y := refined >> (fraction+(if(op == "rcp") 0 else 1))
      if(gold) {
        val (companion,_)=multiply(state.companion,coefficient,state,cursor,multiplyStages)
        update.companion := companion >> (fraction+(if(op == "rcp") 0 else 1))
      }
      state = stage(update,cursor+multiplyStages-1); cursor += multiplyStages
    }
    if(op == "sqrt" && !gold) {
      val m = state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
      val (p,sideband)=multiply(m,state.y,state,cursor,multiplyStages)
      val next = Wire(chiselTypeOf(initial)); next := sideband; next.y := p >> f.fb
      state = stage(next,cursor+multiplyStages-1); cursor += multiplyStages
    }
    val candidate = Wire(chiselTypeOf(initial)); candidate := state
    candidate.candidate := (if(op == "sqrt" && gold) state.companion else state.y) >> (fraction-q)
    state = stage(candidate,cursor); cursor += 1
    if(op == "rsqrt" && multiplyStages > 1) {
      val (p,sideband)=multiply(state.candidate,state.candidate,state,cursor,multiplyStages)
      val next = Wire(chiselTypeOf(initial)); next := sideband; next.product := p
      state=stage(next,cursor+multiplyStages-1);cursor+=multiplyStages
    }
    val m = if(op == "rcp") state.a.sig else state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
    val (p,sideband) = if(op == "rcp") multiply(m,state.candidate,state,cursor,multiplyStages)
      else if(op == "sqrt") multiply(state.candidate,state.candidate,state,cursor,multiplyStages)
      else if(multiplyStages > 1) multiply(m,state.product(2*q+3,0),state,cursor,multiplyStages)
      else (m*(state.candidate*state.candidate),state)
    val residual = Wire(chiselTypeOf(initial)); residual := sideband; residual.product := p
    state = stage(residual,cursor+multiplyStages-1); cursor += multiplyStages
    if(op == "rsqrt" && multiplyStages > 1) {
      val denominator = state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
      val (d,sideband)=multiply(denominator,(state.candidate << 1)+1.U,state,cursor,multiplyStages)
      val next = Wire(chiselTypeOf(initial)); next := sideband; next.delta := d
      state=stage(next,cursor+multiplyStages-1);cursor+=multiplyStages
    }
    val denominator = if(op == "rcp") state.a.sig else state.a.sig << (state.a.exp+f.fb.S).asUInt(0)
    val target = if(op == "sqrt") denominator << (2*q-f.fb) else (BigInt(1) << (if(op == "rcp") q+f.fb else 2*q+f.fb)).U
    val deltaUp = if(op == "rcp") denominator else if(op == "sqrt") (state.candidate << 1)+1.U else if(multiplyStages>1) state.delta else denominator*((state.candidate << 1)+1.U)
    val deltaDown = if(op == "rcp") denominator else if(op == "sqrt") (state.candidate << 1)-1.U else if(multiplyStages>1) state.delta-(denominator << 1) else denominator*((state.candidate << 1)-1.U)
    val tooHigh = state.product > target
    val tooLow = state.product+&deltaUp <= target
    val corrected = Mux(tooHigh,state.candidate-1.U,Mux(tooLow,state.candidate+1.U,state.candidate))
    val classifyExact = cfg.obj.get("exactness_classification").exists(_.bool) && op != "sqrt"
    val correctedProduct = if(classifyExact) 0.U else
      Mux(tooHigh,state.product-deltaDown,Mux(tooLow,state.product+deltaUp,state.product))
    val raw = if(classifyExact) {
      // A reciprocal is dyadic exactly when its normalized significand is one.
      // A reciprocal square root additionally needs an even binary exponent.
      // This decides the exact residual's zero predicate without another wide sum.
      val exact = state.a.sig === (BigInt(1) << f.fb).U &&
        (if(op == "rsqrt") !(state.a.exp+f.fb.S).asUInt(0) else true.B)
      Cat(corrected,!exact)
    } else {
      Cat(corrected,correctedProduct =/= target)
    }
    val rounded = stage(magnitude(state.a,state.meta,raw),cursor); cursor += 1
    io.out.bits := pipedRound(rounded,q+2,cursor)
    require(s.latency == cursor+3)
  }
}

class FpRcp(f: Format,s: Spec) extends FpAlgebraic(f,s,"rcp")
class FpSqrt(f: Format,s: Spec) extends FpAlgebraic(f,s,"sqrt")
class FpRsqrt(f: Format,s: Spec) extends FpAlgebraic(f,s,"rsqrt")

class FpExp(f: Format,s: Spec) extends UnaryElastic(f,s) {
  val cfg = SfuResources.config(f,s)("exp")
  val fraction = cfg("fraction_bits").num.toInt
  val cf = cfg("constant_fraction").num.toInt
  val k = cfg("table_bits").num.toInt
  val degree = cfg("degree").num.toInt
  val multiplyStages = SfuResources.config(f,s).obj.get("multiply_stages").map(_.num.toInt).getOrElse(1)
  val rangeStages = SfuResources.config(f,s).obj.get("range_stages").map(_.num.toInt).getOrElse(1)
  require(rangeStages == 1 || rangeStages == 2)
  val coefficients = cfg("coefficients").arr.map(SfuResources.integer).toSeq
  val pw = f.p+cf+1
  class State extends Bundle {
    val a = new Decoded(f); val meta = new Meta(f)
    val product = UInt(pw.W)
    val scaled = UInt((fraction+10).W); val discarded = Bool()
    val exponent = SInt(f.ew.W)
    val index = UInt(math.max(1,k).W); val residual = UInt(fraction.W)
    val polynomial = UInt((fraction+2).W); val table = UInt((fraction+2).W)
  }
  val initial = Wire(new State); initial := 0.U.asTypeOf(initial)
  val a = FloatLogic.decode(io.in.bits.a,f); initial.a := a
  val raw = io.in.bits.a(f.width-2,0)
  def threshold(name: String): UInt = SfuResources.integer(cfg(name)).U
  val overflow = !a.sign && raw >= threshold("overflow")
  val zero = a.sign && raw >= threshold("zero")
  val tiny = a.sign && raw >= threshold("tiny")
  val one = raw <= Mux(a.sign,threshold("one_negative"),threshold("one_positive"))
  val meta = initial.meta
  meta.tag := io.in.bits.tag; meta.rounding := io.in.bits.rounding
  meta.special := a.nan || a.inf || a.zero || overflow || zero || one
  meta.bits := Mux(a.nan,f.nanBits.U,Mux(a.inf,Mux(a.sign,0.U,f.infBits.U),
    Mux(a.zero || one,(f.bias << f.fb).U,Mux(overflow,f.infBits.U,0.U))))
  meta.flags := Mux(a.nan,Mux(a.snan,16.U,0.U),Mux(a.inf || a.zero,0.U,Mux(overflow,5.U,Mux(tiny,3.U,1.U))))
  when(io.in.fire) { assert(io.in.bits.rounding === 0.U,"exp supports RNE only") }
  var state = stage(initial,0)
  var cursor = 1
  val (constantProduct,constantMeta) = multiply(state.a.sig,SfuResources.integer(cfg("log2e")).U((cf+1).W),state,cursor,multiplyStages)
  val product = Wire(new State); product := constantMeta
  product.product := constantProduct
  state = stage(product,cursor+multiplyStages-1); cursor += multiplyStages
  val shift = (cf-fraction).S-state.a.exp
  val right = Mux(shift >= pw.S,pw.U,shift.asUInt)
  val mask = ~((~0.U(pw.W)) << right)(pw-1,0)
  val discarded = (state.product & mask).orR
  val scaled = Mux(shift >= 0.S,state.product >> right,
    (state.product << Mux(-shift >= pw.S,pw.U,(-shift).asUInt))(pw-1,0))
  if(rangeStages == 2) {
    val shifted = Wire(new State); shifted := state
    shifted.scaled := scaled; shifted.discarded := shift > 0.S && discarded
    state = stage(shifted,cursor); cursor += 1
  }
  val reduced = Wire(new State); reduced := state
  val t = Wire(SInt((fraction+10).W))
  if(rangeStages == 2) {
    val size = 1 << log2Ceil(fraction+10)
    val negative = Adders.brentKung(~state.scaled.pad(size),0.U(size.W),size,!state.discarded)
    t := Mux(state.a.sign,negative(fraction+9,0).asSInt,state.scaled.asSInt)
  } else {
    t := Mux(state.a.sign,-scaled.zext-Mux(shift > 0.S && discarded,1.S,0.S),scaled.zext)
  }
  val centered = if(k == 0) t+(BigInt(1) << (fraction-1)).S else t
  reduced.exponent := centered >> fraction
  reduced.index := (if(k == 0) 0.U else t.asUInt(fraction-1,fraction-k))
  reduced.residual := centered.asUInt(fraction-k-1,0)
  reduced.polynomial := coefficients.last.U
  state = stage(reduced,cursor); cursor += 1
  if(k > 0) {
    val lookup = Wire(new State); lookup := state
    lookup.table := SfuResources.rom(cfg("table"),state.index,fraction+2)
    state = stage(lookup,cursor); cursor += 1
  }
  for(c <- coefficients.dropRight(1).reverse) {
    val (p,sideband) = multiply(state.polynomial,state.residual,state,cursor,multiplyStages,Some((c << fraction).U))
    val next = Wire(new State); next := sideband
    next.polynomial := p >> fraction
    state = stage(next,cursor+multiplyStages-1); cursor += multiplyStages
  }
  val (p,sideband) = if(k > 0) multiply(state.polynomial,state.table,state,cursor,multiplyStages) else (state.polynomial,state)
  val magnitude = Wire(new Magnitude(f,fraction+2))
  magnitude.mag := (if(k == 0) p else p >> fraction)
  magnitude.exp := sideband.exponent-fraction.S; magnitude.sign := false.B; magnitude.meta := sideband.meta
  val value = if(k > 0) { val v = stage(magnitude,cursor+multiplyStages-1); cursor += multiplyStages; v } else magnitude
  val normalized = stage(FloatLogic.normalize(value,f,fraction+2),cursor); cursor += 1
  val prepared = stage(FloatLogic.prepare(normalized,f,fraction+2),cursor); cursor += 1
  val answer = FloatLogic.finish(prepared,f)
  val bits = Mux(answer.bits >= f.infBits.U,f.maxBits.U,Mux(answer.bits === 0.U,1.U,answer.bits))
  val minimumNormal = (BigInt(1) << f.fb).U
  val category = Mux(prepared.meta.flags(1),Mux(bits >= minimumNormal,minimumNormal-1.U,bits),Mux(bits < minimumNormal,minimumNormal,bits))
  val finalAnswer = Wire(new Response(f.width)); finalAnswer := answer
  finalAnswer.bits := Mux(prepared.meta.special,prepared.meta.bits,category)
  finalAnswer.flags := prepared.meta.flags
  io.out.bits := stage(finalAnswer,cursor)
  require(s.latency == cursor+1)
}

object GenerateUnary {
  def main(args: Array[String]): Unit = {
    require(args.length == 1,"usage: GenerateUnary OUTPUT_ROOT")
    val root = java.nio.file.Path.of(args(0)).toAbsolutePath
    for(f <- Seq("fp32","fp16","bf16"); op <- Seq("exp","rcp","sqrt","rsqrt"))
      Generate.main(Array(f,op,root.resolve(s"${f}_${op}").toString))
  }
}
