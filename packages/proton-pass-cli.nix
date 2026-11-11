{ pkgs, pkgsUnstable }:
pkgsUnstable.proton-pass-cli.overrideAttrs (old: {
  version = "2.7.0";
  src = pkgs.fetchurl {
    url = "https://proton.me/download/pass-cli/2.7.0/pass-cli-linux-x86_64";
    hash = "sha256-/ZM+dheaWjOylYU+nGaW5J5YT+IUGxqvxjo9eMB5HNs=";
  };
  buildInputs = (old.buildInputs or [ ]) ++ [ pkgs.dbus ];
})
