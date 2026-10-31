# Xyle Crosspoint identity

Crosspoint is the approved Xyle master brand. Its open crossing stays legible in
one colour and at favicon size. Xyle is the primary wordmark; Labs, Trace and
Motion are secondary descriptors. Use the same mark across company products.

The SVGs contain only local vector artwork and outlined lettering. No external
fonts, scripts, bitmaps or fragment IDs are needed. The approved stencil crossing
has been resolved to paths so repeated inline logos cannot collide.

Navy: `#1d3242`; teal: `#126e70`. Dark backgrounds use `#f5f9f9` and `#55cbc5`.
Keep the aspect ratio, clear space and the gap at the crossing. Use the dark
artwork on dark backgrounds and the monochrome mark for single-colour output.

## Trace assets

| Asset | Use |
| --- | --- |
| [Logo](../src/xyle_trace/assets/brand/logo.svg) | README and light report surfaces |
| [Inverse logo](../src/xyle_trace/assets/brand/logo-dark.svg) | Dark surfaces |
| [Mark](../src/xyle_trace/assets/brand/mark.svg) | Compact company identity |
| [Inverse mark](../src/xyle_trace/assets/brand/mark-dark.svg) | Dark compact surfaces |
| [Monochrome mark](../src/xyle_trace/assets/brand/mark-mono.svg) | Inherits currentColor |
| [Favicon](../src/xyle_trace/assets/brand/favicon.svg) | Adapts to browser light/dark preference |
| [Square icon SVG](../src/xyle_trace/assets/brand/icon-square.svg) | Avatars and app icons |
| [Square PNG](../.github/assets/brand/icon-square.png) | 512 × 512 avatar upload |
| [Social card source](../src/xyle_trace/assets/brand/social-preview.svg) | Editable 1280 × 640 card |
| [Social PNG](../.github/assets/brand/social-preview.png) | Ready-to-upload GitHub social preview |

![Trace wordmark](../src/xyle_trace/assets/brand/logo.svg)

## Offline use

HTML reports and the explorer embed the packaged SVGs and favicon. No network
request is required, including from an installed wheel. Example figures and the
editorial demo header carry the same identity; calculations and tool activity
are unchanged.

## GitHub settings

The organization avatar is shared by all Xyle Labs repositories. The owner
uploads the company Crosspoint PNG separately. The Trace descriptor belongs in
Trace documentation and product surfaces. Upload the social PNG under repository
Settings → Social preview after merge. Committing an image does not change either
setting automatically.

## Publication review

SVGs contain vector paths and accessible labels only; no remote resources or
embedded font software. The square PNG contains the mark on navy; the social PNG
contains the product wordmark, existing tagline and public repository address.
They were visually inspected and contain resolution metadata only. Exact binary
hashes and sizes are pinned in `scripts/check_repo.py`, retaining earlier
approvals for history scans. The [recording review](examples/assembly-density/PUBLICATION.md)
covers the demo.
