{ niri }:
assert niri.version == "26.04";
niri.overrideAttrs (previous: {
  patches = (previous.patches or [ ]) ++ [ ./niri-window-pixels.patch ];
})
