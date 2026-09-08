ThisBuild / scalaVersion := "2.13.18"
ThisBuild / version := "0.3.0"
ThisBuild / organization := "org.zirconasic"
name := "zircon-asic"
licenses += ("Apache-2.0", url("https://www.apache.org/licenses/LICENSE-2.0"))
homepage := Some(url("https://github.com/MAdrid1011/Zircon-ASIC"))

Compile / resourceGenerators += Def.task {
  val source = baseDirectory.value.getParentFile / "src" / "zircon_asic" / "data" / "contract.json"
  val target = (Compile / resourceManaged).value / "zircon-contract.json"
  IO.copyFile(source, target)
  val spm = (Compile / resourceManaged).value / "zircon-spm.json"
  IO.copyFile(baseDirectory.value.getParentFile / "src" / "zircon_asic" / "data" / "spm.json", spm)
  val sfu = (Compile / resourceManaged).value / "zircon-sfu.json"
  IO.copyFile(baseDirectory.value.getParentFile / "src" / "zircon_asic" / "data" / "sfu.json", sfu)
  val noticeNames = Seq("LICENSE", "THIRD_PARTY_NOTICES.md") ++
    (baseDirectory.value.getParentFile / "licenses" ** "*.txt").get.map(f => "licenses/" + f.getName)
  val notices = noticeNames.map { name =>
    val output = (Compile / resourceManaged).value / "META-INF" / name
    IO.copyFile(baseDirectory.value.getParentFile / name, output)
    output
  }
  Seq(target, spm, sfu) ++ notices
}.taskValue

val chiselVersion = "7.15.0"
libraryDependencies ++= Seq(
  "org.chipsalliance" %% "chisel" % chiselVersion,
  "com.lihaoyi" %% "ujson" % "3.3.1"
)
addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chiselVersion cross CrossVersion.full)
scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked", "-language:reflectiveCalls")
