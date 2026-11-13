sudo is the generated weasel accepted for this project on 2026-11-11.
It is not the identity of any other animal. Other species are unavailable until
their own validated packs are supplied; custom imports remain explicit overrides.

Build inputs (Imagegen, same approved character):
- source.png: original four-column/eight-row atlas,
  SHA256 06a84e76571c91bf47d39f0acc42c33b67fdf714432e432592b8bf36278b3403.
- question-answer-source.png: four-column/two-row gesture correction,
  SHA256 cae6f5612a345a100dbe7ab6855aa1b9eb19e1d4a6836c66c690c55f353edb9b.

`pet-asset-compiler --sudo source.png question-answer-source.png pet.png`
normalizes each cell independently to 256px. The original supplies rows 0–4 and
7; correction row 0 supplies row 5, correction row 1 supplies row 6. The shared
replacement scale is 0.94 relative to its cells, matching the original figure's
height. Feet are registered to y=248 and their bottom-band center to x=128.
No body or head is separately stretched. Low-alpha background (0–16) is removed;
remaining alpha is rescaled and interpolated in premultiplied form. This avoids
colored transparent RGB and neighbouring cells contaminating fur edges.

Rows retain the custom-pack ABI names. `waiting` is an unanswered approval,
`attention` a question, and `success` offers a completed answer without declaring
task success. Four frames at 6fps settle on frame 3. Native rendering replaces
all disabled/unknown artwork with opaque eye protection and both covering paws;
generated pixels never establish permission or satisfy that protection.

The static 144px light/dark contact sheet was inspected. That is asset evidence,
not a live desktop, temporal animation or real-thread workflow acceptance.
