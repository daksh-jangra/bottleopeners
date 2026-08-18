# Display font (optional)

The dashboard's big numbers use a condensed display face. By default it falls
back to the platform's condensed cut (e.g. Helvetica Neue Condensed on macOS)
via `font-stretch: condensed`.

For a consistent look on every OS, drop a WOFF2 here:

    static/fonts/ArchivoNarrow-Bold.woff2

Archivo Narrow is under the SIL Open Font License. Get it from Google Fonts
(https://fonts.google.com/specimen/Archivo+Narrow) and convert the Bold weight
to WOFF2. The `@font-face` in templates/dashboard.html already points here.
