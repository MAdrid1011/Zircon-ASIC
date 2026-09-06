package zircon

import chisel3._
import circt.stage.ChiselStage
import java.nio.file.{Files,Path}

object Generate {
 def main(args: Array[String]): Unit = {
  require(args.length >= 3,"usage: runMain zircon.Generate FORMAT OP OUTPUT_DIR [signed|unsigned]")
  val name = args(0); val op = args(1); val out = Path.of(args(2)).toAbsolutePath
  val signed = args.length < 4 || args(3) != "unsigned"
  val contract = sys.env.get("ZIRCON_CONTRACT").map(p => new Contract(Path.of(p))).getOrElse(Contract.bundled())
  val s = contract.spec(name,op)
  def module(): RawModule = Arithmetic(name,op,signed,contract)
  Files.createDirectories(out)
  val sv = ChiselStage.emitSystemVerilog(module(),firtoolOpts=Array("-disable-all-randomization","-strip-debug-info","--default-layer-specialization=enable","--lowering-options=disallowLocalVariables,disallowPackedArrays"))
  Files.writeString(out.resolve("Unit.sv"),sv)
  val hash = java.security.MessageDigest.getInstance("SHA-256").digest(sv.getBytes(java.nio.charset.StandardCharsets.UTF_8)).map(b => f"${b & 255}%02x").mkString
  Files.writeString(out.resolve("manifest.json"),ujson.write(ujson.Obj("format"->name,"operation"->op,"signed"->signed,"latency"->s.latency,"kind"->s.kind,"variant"->s.variant,"phases"->ujson.Arr.from(s.phases),"contract"->contract.json,"rtl_sha256"->hash,"qualification"->"unqualified"),indent=2))
}
}

object GenerateAll {
 def main(args: Array[String]): Unit = {
  val c = new Contract(Path.of("../src/zircon_asic/data/contract.json"))
  val root = Path.of(args(0)).toAbsolutePath
  for(key <- c.json("units").obj.keys) {
    val parts = key.split("\\."); val name = parts(0); val op = parts(1)
    Generate.main(Array(name,op,root.resolve(s"${name}_${op}").toString))
    if(name.startsWith("int")) Generate.main(Array(name,op,root.resolve(s"${name}_${op}_unsigned").toString,"unsigned"))
  }
 }
}
