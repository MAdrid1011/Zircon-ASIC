package zircon

/** Instantiate with Module(Arithmetic("fp32", "fma")) in a parent design. */
object Arithmetic {
  def apply(name: String,op: String,signed: Boolean = true,contract: Contract = Contract.bundled()): ArithmeticModule = {
    val s = contract.spec(name,op)
    if(name.startsWith("int")) {
      val width = name.drop(3).toInt
      op match {
        case "add" => new IntAdd(width,signed,s)
        case "mul" => new IntMul(width,signed,s)
        case "div" => new IntDiv(width,signed,s)
      }
    } else {
      val f = contract.format(name)
      if(name == "e2m1") {
        if(op == "div") new Fp4Div(f,s) else new Fp4Arithmetic(f,s,op)
      } else op match {
        case "add" => new FpAdd(f,s)
        case "mul" => new FpMul(f,s)
        case "fma" => new FpFma(f,s)
        case "div" => new FpDiv(f,s)
      }
    }
  }
}
