# Swiperboxd web files

The public HTML, JavaScript, CSS, SVG and web manifest are deployed as static
Vercel files. Existing `/`, `/favicon.ico` and `/web/` asset URLs reach those
files before the Python API route, so page and asset delivery do not start a
Function. Security headers are preserved. Browsers revalidate these unversioned
files on navigation so a deployment does not leave an old script cached.

The API still serves personal data and handles synchronization through FastAPI;
those responses do not receive shared static-file caching. Local FastAPI
development continues to serve these files from this directory.
