# Follow-up: object-gate false positives on figure-absent beats

Logged only — do not action on this close.

The object-gate INTRUDER classifier is over-triggering on ordinary scene objects once `composition_type` variety forces 5/9 beats to `object_focus` or `wide_environment` (no human figure).

Confirmed false positives in the riso closing pack:

- communication scene 2 chairs (mug/laptop-class)
- communication scene 6 park bench
- loneliness scene 8 empty chair (“garbled letters”)
- forgiveness scene 2 bag (mug/laptop-class)
- forgiveness scene 6 river wall / landscape (object + intruder)
- forgiveness scene 9 empty platform / rain-on-window (cart/signs class; second regen was a clean wide interior, no cart or signs)

Real fails still worth the gate: ticket typography, mug-instead-of-napkin, baked-in signatures, platform carts/signs.

Calibrate before `lora_surreal_retro_vintage_painting` inherits this mix at higher volume.
