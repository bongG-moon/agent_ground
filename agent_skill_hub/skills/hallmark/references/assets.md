# Enterprise-safe assets

Load this file only when the design genuinely needs an asset. Prefer a strong typography-only result over introducing an unapproved dependency.

## Allowed sources

Use sources in this order:

1. Assets supplied by the user for the current task.
2. Assets already committed to the project.
3. Company-approved DAM, font library, icon set, or package mirror.
4. A public reference downloaded through the approved read-only ingress process, licence-reviewed, scanned, and then vendored into the project.

Never upload prompts, screenshots, brand assets, source code, internal URLs, or company data to an external asset or AI-generation service. Never reference a public CDN, remote font stylesheet, public image API, or remotely hosted Lottie file from the delivered application.

## Import pattern

- Copy the approved asset into the repository.
- Use a stable local path and a descriptive filename.
- Record source, licence, and acquisition date in the project's existing asset manifest when one exists.
- Optimise locally and preserve the original when the project convention requires it.
- Provide useful `alt` text for informative images and empty `alt` text for decoration.

```html
<img src="/assets/product/hero.webp" alt="Approved product view" />
```

Do not emit `https://` asset URLs in application markup or CSS.

## Safe fallbacks

- Product, team, property, food, and portfolio photography: use a clearly labelled local placeholder until approved media is supplied.
- Icons: use the project's installed icon package or a small hand-built SVG that is committed locally.
- Fonts: use locally hosted, licence-approved font files or the system font stack.
- Illustration: prefer CSS art or hand-built SVG. Otherwise wait for an approved supplied asset.
- Motion: prefer CSS or SVG. Use Lottie only when its JSON and renderer package are already approved and vendored locally.

## Final check

- [ ] Every asset resolves locally or through an approved internal service
- [ ] No public CDN, font service, image API, or external AI service is referenced
- [ ] No confidential data left the approved model and network boundary
- [ ] Licence and provenance are recorded
- [ ] Asset size, accessibility, and reduced-motion behavior are verified
