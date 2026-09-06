package zircon

import chisel3._
import chisel3.util._

object Adders {
  def brentKung(a: UInt, b: UInt, w: Int, cin: Bool = false.B): UInt = {
    val p0 = (0 until w).map(i => a(i) ^ b(i))
    var gp = (0 until w).map(i => (a(i) && b(i), p0(i))).toVector
    gp = gp.updated(0,(gp(0)._1 || (p0(0) && cin),p0(0)))
    def combine(i: Int, j: Int): Unit = {
      val (g, p) = gp(i); val (h, q) = gp(j)
      gp = gp.updated(i, (g || (p && h), p && q))
    }
    var stride = 2
    while (stride <= w) {
      for (i <- stride-1 until w by stride) combine(i, i-stride/2)
      stride *= 2
    }
    stride = w/2
    while (stride >= 2) {
      for (i <- stride+stride/2-1 until w by stride) combine(i, i-stride/2)
      stride /= 2
    }
    Cat(gp.last._1, VecInit((0 until w).map(i => p0(i) ^ (if (i == 0) cin else gp(i-1)._1))).asUInt)
  }
  def signedAdd(a: SInt,b: SInt,w: Int,subtract: Boolean = false): SInt = {
    val size = 1 << log2Ceil(w)
    val aa = Wire(SInt(size.W)); val bb = Wire(SInt(size.W)); aa := a; bb := b
    brentKung(aa.asUInt,if(subtract) ~bb.asUInt else bb.asUInt,size,subtract.B)(w-1,0).asSInt
  }
  def grouped(a: UInt, b: UInt, w: Int): UInt = {
    val p = (0 until w).map(i => a(i) ^ b(i))
    val g = (0 until w).map(i => a(i) && b(i))
    val carries = Wire(Vec(w+1, Bool())); carries(0) := false.B
    for (i <- 1 to w) {
      val base = ((i-1)/4)*4
      val terms = (base until i).map(j => g(j) && ((j+1 until i).map(p).foldLeft(true.B)(_ && _)))
      carries(i) := terms.reduce(_ || _) || ((base until i).map(p).reduce(_ && _) && carries(base))
    }
    Cat(carries(w), VecInit((0 until w).map(i => p(i) ^ carries(i))).asUInt)
  }
}

class IntAdd(w: Int, signed: Boolean, s: Spec) extends ElasticModule(w, s) {
  val a = io.in.bits.a; val b = io.in.bits.b
  val sum = if (w == 32) Adders.brentKung(a,b,w) else if (w == 16) Adders.grouped(a,b,w) else a +& b
  val overflow = if (signed) (a(w-1) === b(w-1)) && (sum(w-1) =/= a(w-1)) else sum(w)
  io.out.bits := stage(result(sum(w-1,0), Mux(overflow, 4.U, 0.U), io.in.bits.tag), 0)
}

object Compressors {
  // Column Dadda reduction: each level limits the bit heap to its target height.
  def reduce(rows: Seq[UInt], width: Int, target: Int): Seq[UInt] = {
    val columns = Array.tabulate(width)(i => scala.collection.mutable.ArrayBuffer.from(rows.map(_(i))))
    val output = Array.fill(width)(scala.collection.mutable.ArrayBuffer.empty[Bool])
    for (i <- 0 until width) {
      val col = columns(i)
      while (col.size + output(i).size > target) {
        if (col.size + output(i).size == target+1 || col.size == 2) {
          val a = col.remove(0); val b = col.remove(0)
          output(i) += (a ^ b)
          if (i+1 < width) output(i+1) += (a && b)
        } else {
          val a = col.remove(0); val b = col.remove(0); val c = col.remove(0)
          output(i) += (a ^ b ^ c)
          if (i+1 < width) output(i+1) += ((a && b) || (a && c) || (b && c))
        }
      }
      output(i) ++= col
    }
    (0 until target).map(j => VecInit((0 until width).map(i => if (j < output(i).size) output(i)(j) else false.B)).asUInt)
  }
  def targets(n: Int): Seq[Int] = {
    var seq = Vector(2)
    while (seq.last < n) seq :+= seq.last*3/2
    seq.dropRight(1).reverse
  }
  def baugh(a: UInt,b: UInt,w: Int,signed: Boolean): Seq[UInt] = {
    val rows = (0 until w).map(j => VecInit((0 until 2*w).map { k =>
      val i = k-j
      if (i < 0 || i >= w) false.B
      else { val bit = a(i) && b(j); if (signed && ((i == w-1) ^ (j == w-1))) !bit else bit }
    }).asUInt)
    if (signed) rows :+ ((BigInt(1) << w) | (BigInt(1) << (2*w-1))).U((2*w).W) else rows
  }
  def booth(a: UInt,b: UInt,w: Int,signed: Boolean): Seq[UInt] = {
    val aw = if (signed) Cat(a(w-1),a).asSInt else Cat(0.U(1.W),a).asSInt
    val bw = Cat(if (signed) Fill(2,b(w-1)) else 0.U(2.W),b,0.U(1.W))
    (0 until (w+2)/2).map { i =>
      val code = bw(2*i+2,2*i)
      val value = Wire(SInt((2*w+2).W))
      value := MuxLookup(code, 0.S)(Seq(1.U -> aw, 2.U -> aw, 3.U -> (aw << 1), 4.U -> -(aw << 1), 5.U -> -aw, 6.U -> -aw))
      (value.asUInt << (2*i))(2*w-1,0)
    }
  }
}

class IntMul(w: Int, signed: Boolean, s: Spec) extends ElasticModule(w, s) {
  val rows = if (w == 8) Compressors.baugh(io.in.bits.a,io.in.bits.b,w,signed) else Compressors.booth(io.in.bits.a,io.in.bits.b,w,signed)
  val targets = Compressors.targets(rows.size)
  var heap = rows
  val cuts = s.latency-1
  var tag = io.in.bits.tag
  for (i <- 0 until cuts) {
    for (t <- targets.slice(targets.size*i/cuts, targets.size*(i+1)/cuts)) heap = Compressors.reduce(heap,2*w,t)
    heap = stage(VecInit(heap),i).toSeq
    tag = stage(tag,i)
  }
  val full = heap.reduce(_ + _)
  val overflow = if (signed) full(2*w-1,w) =/= Fill(w,full(w-1)) else full(2*w-1,w).orR
  io.out.bits := stage(result(full(w-1,0),Mux(overflow,4.U,0.U),tag),s.latency-1)
}

class IntDiv(w: Int, signed: Boolean, s: Spec) extends IterativeModule(w, s) {
  val a = Reg(UInt(w.W)); val b = Reg(UInt(w.W))
  val original = Reg(UInt(w.W)); val tag = Reg(UInt(32.W))
  val negative = Reg(Bool()); val negR = Reg(Bool())
  val dz = Reg(Bool()); val ov = Reg(Bool())
  val rem = Reg(SInt((w+5).W)); val quotient = Reg(SInt((w+3).W))
  val stream = Reg(UInt(w.W)); val divisor = Reg(UInt((w+1).W)); val shift = Reg(UInt(log2Ceil(w+1).W))
  val out = Reg(new Response(w)); io.out.bits := out
  when(io.in.fire) {
    val na = if (signed) io.in.bits.a(w-1) else false.B
    val nb = if (signed) io.in.bits.b(w-1) else false.B
    a := Mux(na, -io.in.bits.a, io.in.bits.a)
    b := Mux(nb, -io.in.bits.b, io.in.bits.b)
    original := io.in.bits.a; tag := io.in.bits.tag
    negative := na ^ nb; negR := na
    dz := io.in.bits.b === 0.U
    ov := (if (signed) io.in.bits.a === (BigInt(1) << (w-1)).U && io.in.bits.b.andR else false.B)
    if (w == 8) {
      rem := 0.S; quotient := 0.S
      stream := Mux(na,-io.in.bits.a,io.in.bits.a)
      divisor := Mux(nb,-io.in.bits.b,io.in.bits.b)
    }
  }
  val correctedQ = Wire(UInt(w.W))
  if (w > 8) {
    val high = Reg(UInt(w.W))
    val positive = Reg(UInt((w+3).W)); val negativeDigits = Reg(UInt((w+3).W))
    val triple = Reg(SInt((w+4).W)); val negTriple = Reg(SInt((w+4).W)); val negDivisor = Reg(SInt((w+2).W))
    when(phase === 1.U && !cancel) {
      val clz = PriorityEncoder(Reverse(b))
      val normD = (b << clz)(w-1,0)
      val normA = (a.pad(2*w) << clz)(2*w-1,0)
      high := normA(2*w-1,w)
      divisor := normD; shift := clz; stream := normA(w-1,0)
    }
    when(phase === s.phases.indexOf("seed").U && !cancel) {
      val d = divisor.zext
      val seed = (high << 1) >= divisor
      val difference = Adders.signedAdd(high.zext,d,w+5,true)
      rem := Mux(seed,difference,high.zext)
      positive := seed; negativeDigits := 0.U
      val three = Adders.signedAdd(d,d << 1,w+4)
      triple := three; negTriple := Adders.signedAdd(0.S,three,w+4,true)
      negDivisor := -d
    }
    for (i <- 0 until s.iterations) when(phase === (i+s.phases.indexOf("iterate")).U && !cancel) {
      val x = Cat(rem.asUInt,stream(w-1,w-2)).asSInt
      val twice = x << 1; val d = divisor.zext
      val digit = Mux(twice >= triple,2.S,Mux(twice >= d,1.S,Mux(twice <= negTriple,(-2).S,Mux(twice <= negDivisor,(-1).S,0.S))))
      val minusOne = Adders.signedAdd(x,d,w+5,true)
      val minusTwo = Adders.signedAdd(x,d << 1,w+5,true)
      val plusOne = Adders.signedAdd(x,d,w+5)
      val plusTwo = Adders.signedAdd(x,d << 1,w+5)
      rem := Mux(digit === 2.S,minusTwo,Mux(digit === 1.S,minusOne,Mux(digit === (-1).S,plusOne,Mux(digit === (-2).S,plusTwo,x))))
      positive := Cat(positive(w,0),Mux(digit >= 0.S,digit.asUInt(1,0),0.U(2.W)))
      negativeDigits := Cat(negativeDigits(w,0),Mux(digit < 0.S,(-digit).asUInt(1,0),0.U(2.W)))
      stream := stream << 2
    }
    val qw = w+3; val size = 1 << log2Ceil(qw)
    correctedQ := Adders.brentKung(positive.pad(size),(~negativeDigits).pad(size),size,!(rem < 0.S))(w-1,0)
  } else {
    correctedQ := quotient.asUInt
    for (i <- 0 until 8) when(phase === (i+1).U && !cancel) {
      val shifted = (rem << 1) + stream(w-1).asUInt.zext
      val next = Mux(rem >= 0.S,shifted-divisor.zext,shifted+divisor.zext)
      rem := next; quotient := (quotient << 1)+(next >= 0.S).asUInt.zext; stream := stream << 1
    }
  }
  val correctionPhase = s.phases.indexOf("correct")
  val correctedR = Mux(rem < 0.S,if(w>8) Adders.signedAdd(rem,divisor.zext,w+5) else rem+divisor.zext,rem).asUInt
  val q = Reg(UInt(w.W)); val r = Reg(UInt(w.W))
  def finish(qmag: UInt,rmag: UInt): Unit = {
    out.bits := Mux(dz,Fill(w,1.U(1.W)),Mux(negative,-qmag,qmag))
    out.remainder := Mux(dz,original,Mux(negR,-rmag,rmag))
    out.flags := Mux(dz,8.U,Mux(ov,4.U,0.U)); out.tag := tag
  }
  when(phase === correctionPhase.U && !cancel) {
    if (w == 8) finish(correctedQ.asUInt(w-1,0),correctedR(w-1,0))
    else { q := correctedQ.asUInt; r := correctedR >> shift }
  }
  if (w > 8) when(phase === (s.latency-1).U && !cancel) { finish(q,r) }
}
