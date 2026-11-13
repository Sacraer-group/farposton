# Visual search check

Date: 2026-11-13. Model `google/siglip2-base-patch16-224`, CPU only (6 cores, no GPU), transformers 5.23, torch 2.15 CPU.

## Run

`flats demo && flats embed` on the 50 real offers: 803 photos embedded in 12 min 6 s (~0.9 s per photo incl. download). First run also downloads the model (~75 s). Vectors are L2-normalized, 768 dims.

## Quality proxy

Zero-shot room-type labels from the same model (kitchen, bathroom, bedroom, living room, empty room, exterior, window view, hallway, floor plan).
For every photo, the top-5 most similar photos **from other flats** have the same room type in **77.8%** of cases; random baseline 12.6%.

Mean cosine similarity: photos of the same flat 0.750, of different flats 0.718.

## Verdict

Keep the model. It finds the same kind of room reliably. Not checked by eye: whether it matches style (light/dark, renovation level) and not just room type; the agent environment cannot view images.

## Known limits

Exteriors, window views and floor plans are also matched; a user who picks a kitchen photo gets kitchens. Picking an exterior gives exteriors.
