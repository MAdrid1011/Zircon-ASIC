package zircon

import java.nio.file.{Files, Path}

case class Format(name: String, width: Int, eb: Int, fb: Int, bias: Int, encoding: String) {
  val p = fb + 1
  val ew = eb + 3 // includes product exponents and bounded alignment offsets
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
class Contract private (val json: ujson.Value) {
  def this(path: Path) = this(ujson.read(Files.readString(path)))
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

object Contract {
  def bundled(): Contract = {
    val stream = getClass.getResourceAsStream("/zircon-contract.json")
    require(stream != null,"shared contract resource is missing from the hardware library")
    try new Contract(ujson.read(new String(stream.readAllBytes(),java.nio.charset.StandardCharsets.UTF_8)))
    finally stream.close()
  }
}
