// Where the API lives. Empty string means "same origin", which is what the
// local desktop app wants - Flask serves this directory and the API from one
// port, so no configuration is needed to run it on your own machine.
//
// A split deployment (static frontend on a CDN, Flask backend elsewhere)
// overrides this at deploy time with the backend's absolute URL, e.g.
//
//     window.ETAP_API_BASE = 'https://api.etaplens.example.com';
//
// No trailing slash - it is concatenated directly with paths like '/api/...'.
window.ETAP_API_BASE = '';
