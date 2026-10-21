{ pkgs, pkgsUnstable }:
pkgsUnstable.proton-pass-cli.overrideAttrs (old: {
  version = "2.5.0";
  src = pkgs.fetchurl {
    url = "https://proton.me/download/pass-cli/2.5.0/pass-cli-linux-x86_64";
    hash = "sha256-nOkl0GQHCXM3oBUC8oHWOovtgLfFS5aULdZl1OtIjP0=";
  };
  buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.dbus ];
})
