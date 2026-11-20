{ inputs, ... }:
{
  flake.nixosModules.agent-first = import ../../modules/agent-first.nix;
  flake.nixosConfigurations.weasel-agent = inputs.nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [ ../../hosts/weasel-agent/config.nix ];
  };
  perSystem =
    { system, ... }:
    let
      pkgs = inputs.nixpkgs.legacyPackages.${system};
      testPkgs = import inputs.nixpkgs {
        inherit system;
        config.allowUnfreePredicate = pkg: pkgs.lib.getName pkg == "chatgpt";
      };
    in
    {
      apps.weasel-agent = {
        type = "app";
        program = "${inputs.self.nixosConfigurations.weasel-agent.config.system.build.vm}/bin/run-weasel-agent-vm";
        meta.description = "Start the standalone Weasel agent-first Niri VM";
      };
      packages.agent-workspace = pkgs.callPackage ../../packages/agent-workspace { };
      packages.agent-desktop = pkgs.callPackage ../../packages/agent-desktop { };
      checks.agent-first-desktop = testPkgs.callPackage ../../tests/agent-first { };
    };
}
