package zircon

import java.nio.file.{Files, Path}

case class Format(name: String, width: Int, eb: Int, fb: Int, bias: Int, encoding: String) {
  val p = fb + 1
  val emin = 1 - bias
  val emax = (1 << eb) - (if (encoding == "ieee") 2 else 1) - bias
  val maxBits: BigInt = (BigInt(1) << (width-1)) - 1 - (encoding match {
    case "ieee" => BigInt(1) << fb
    case "finite_nan" => BigInt(1)
    case _ => BigInt(0)
  })
  val infBits: BigInt = BigInt((1 << eb)-1) << fb
  val nanBits: BigInt = encoding match {
    case "ieee" => infBits | (BigInt(1) << (fb-1))
    case "finite_nan" => (BigInt(1) << (width-1))-1
    case _ => BigInt(0)
  }
}
case class Spec(name: String, op: String, latency: Int, kind: String, phases: Seq[String], variant: String) {
  val iterations = phases.count(_ == "iterate")
}
class Contract(path: Path) {
  val json = ujson.read(Files.readString(path))
  require(json("schema_version").num == 1 && json("interface_version").num == 1)
  def spec(name: String, op: String): Spec = {
    val v = json("units")(s"$name.$op")
    Spec(name, op, v("latency").num.toInt, v("kind").str, v("phases").arr.map(_.str).toSeq, v("variant").str)
  }
  def format(name: String): Format = {
    val v = json("formats")(name)
    Format(name, v("width").num.toInt, v("exponent").num.toInt, v("fraction").num.toInt, v("bias").num.toInt, v("encoding").str)
  }
}
