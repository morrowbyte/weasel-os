{ pkgs, pkgsUnstable }:
pkgsUnstable.proton-pass-cli.overrideAttrs (old: {
  version = "2.5.1";
  src = pkgs.fetchurl {
    url = "https://proton.me/download/pass-cli/2.5.1/pass-cli-linux-x86_64";
    hash = "sha256-QOJ2ReBIX18NuSi4FGnRxafIlooWKVbpQAaUdiRMArE=";
  };
  buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.dbus ];
})
