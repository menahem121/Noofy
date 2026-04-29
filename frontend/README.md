# Noofy Frontend

React and TypeScript frontend for the Noofy desktop app.

## Structure

- `src/features/home/`: the real Home Page screen and its tests.
- `src/lib/api/`: small backend API client. Frontend code calls Noofy `/api` endpoints only.
- `src/styles/`: app-wide design tokens and layout styles.

## Development

```bash
npm install
npm run dev
```

The dev server runs at `http://127.0.0.1:5173/` and proxies `/api` to the local backend at `http://127.0.0.1:8000`.

Override the API base URL with `VITE_NOOFY_API_BASE_URL` when the desktop shell uses a different backend URL.

## Checks

```bash
npm test
npm run build
```
