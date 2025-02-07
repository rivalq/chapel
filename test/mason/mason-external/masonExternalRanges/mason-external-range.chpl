use MasonModify;
use MasonExternal;
use MasonBuild;
use MasonInit;

proc main() {
  // Sets up Spack
  const setupArgs = ["external","--setup"];
  masonExternal(setupArgs);
  // Update compilers.yaml for this system
  var compilerFindArgs = ["external", "compiler", "--find"];
  masonExternal(compilerFindArgs);
  // Download and install libtomlc99
  var installArgs: [0..3] string = ["install", "libtomlc99@0.2019.06.24"];
  installSpkg(installArgs);
  // Build library that uses libtomlc99
  var buildArgs: [0..2] string = ["mason", "build", "--force"];
  masonBuild(buildArgs);
}
