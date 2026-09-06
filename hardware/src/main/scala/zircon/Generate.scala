package zircon

import chisel3._
import circt.stage.ChiselStage
import java.nio.file.{Files,Path}

object Generate extends App {
  require(args.length >= 3,"usage: runMain zircon.Generate FORMAT OP OUTPUT_DIR [signed|unsigned]")
  val name = args(0); val op = args(1); val out = Path.of(args(2)).toAbsolutePath
  val signed = args.length < 4 || args(3) != "unsigned"
  val path = sys.env.get("ZIRCON_CONTRACT").map(Path.of(_)).getOrElse(Path.of("../src/zircon_asic/data/contract.json"))
  val contract = new Contract(path); val s = contract.spec(name,op)
  def module(): RawModule = {
    if (name.startsWith("int")) {
      val w = name.drop(3).toInt
      op match {
        case "add" => new IntAdd(w,signed,s)
        case "mul" => new IntMul(w,signed,s)
        case "div" => new IntDiv(w,signed,s)
      }
    } else {
      val f = contract.format(name)
      op match {
        case "add" => new FpAdd(f,s)
        case "mul" => new FpMul(f,s)
        case "fma" => new FpFma(f,s)
        case "div" => if (name == "e2m1") new Fp4Div(f,s) else new FpDiv(f,s)
      }
    }
  }
  Files.createDirectories(out)
  val sv = ChiselStage.emitSystemVerilog(module(),firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays"))
  Files.writeString(out.resolve("Unit.sv"),sv)
  Files.writeString(out.resolve("manifest.json"),ujson.write(ujson.Obj("format"->name,"operation"->op,"signed"->signed,"latency"->s.latency,"kind"->s.kind,"variant"->s.variant,"phases"->ujson.Arr.from(s.phases),"contract"->contract.json,"qualification"->"unqualified"),indent=2))
}
