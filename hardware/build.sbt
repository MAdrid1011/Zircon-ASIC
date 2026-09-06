ThisBuild / scalaVersion := "2.13.18"
ThisBuild / version := "0.1.0"
ThisBuild / organization := "org.zirconasic"

val chiselVersion = "7.15.0"
libraryDependencies ++= Seq(
  "org.chipsalliance" %% "chisel" % chiselVersion,
  "com.lihaoyi" %% "ujson" % "3.3.1"
)
addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % chiselVersion cross CrossVersion.full)
scalacOptions ++= Seq("-deprecation", "-feature", "-unchecked", "-language:reflectiveCalls")
