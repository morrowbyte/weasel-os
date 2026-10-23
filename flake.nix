{
  description = "WeaselOS";

  inputs = {
    flake-parts.url = "github:hercules-ci/flake-parts";
    disko = {
      url = "github:nix-community/disko";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixpkgs-unstable.url = "github:nixos/nixpkgs/nixos-unstable";
    serena = {
      url = "github:oraios/serena/5e4f5cd173c7ff3eed37ee3f1de6c266b937c5dc";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    nixpkgs.url = "github:nixos/nixpkgs/nixos-26.05";
    claude-desktop = {
      url = "github:tomsch/claude-desktop-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-anywhere = {
      url = "github:nix-community/nixos-anywhere";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-openclaw = {
      url = "github:openclaw/nix-openclaw";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nixos-hardware.url = "github:NixOS/nixos-hardware/master";
    fine-cmdline = {
      url = "github:VonHeikemen/fine-cmdline.nvim";
      flake = false;
    };
    quickshell = {
      url = "git+https://git.outfoxxed.me/outfoxxed/quickshell";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    dms = {
      url = "github:AvengeMedia/DankMaterialShell/stable";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    helium = {
      url = "github:schembriaiden/helium-browser-nix-flake";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    zen-browser = {
      url = "github:youwen5/zen-browser-flake";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    millennium.url = "github:SteamClientHomebrew/Millennium?dir=packages/nix";
    t3code = {
      url = "path:./packages/t3code";
      inputs.nixpkgs.follows = "nixpkgs-unstable";
    };
    hermes-agent.url = "github:NousResearch/hermes-agent/v0.22.5";
    handy-nixpkgs.url = "github:NixOS/nixpkgs/46db2e09e1d3f113a13c0d7b81e2f221c63b8ce9";
    handy = {
      # Pin to the immutable upstream v0.9.6 release with its matching nixpkgs snapshot
      url = "github:cjpais/Handy/14f6f0d31cb22a4268acfbddba3052820fd0a06e";
      inputs.nixpkgs.follows = "handy-nixpkgs";
    };
    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    inputs:
    inputs.flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [ "x86_64-linux" ];
      imports = [
        ./flake/modules/shared.nix
        ./flake/modules/outputs.nix
        ./flake/modules/nixos-configurations.nix
        ./flake/modules/module-exports.nix
      ];
    };
}
