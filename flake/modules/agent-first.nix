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
    in
    {
      packages.agent-workspace = pkgs.callPackage ../../packages/agent-workspace { };
      packages.agent-desktop = pkgs.callPackage ../../packages/agent-desktop { };
      checks.agent-first-desktop = pkgs.callPackage ../../tests/agent-first { };
    };
}
