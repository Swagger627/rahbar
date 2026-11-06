# Chat UI screenshots (Step 5.1, 2026-11-06)

Every screenshot shows a **real** answer: the real API, retrieving from the real 431-chunk
index with `nomic-embed-text`, and the answer written by `llama3.2:3b` through Ollama. No
answer, citation or badge was typed in by hand or faked. The same question can be answered
differently from one run to the next, because the model is not deterministic.

| File | What it shows | Where it ran |
| --- | --- | --- |
| `01-desktop-light-welcome.png` | the start screen (day map) | local API serving `ui/` |
| `02-desktop-light-answer-cache-miss.png` | a grounded answer, its source ticket, confidence, `X-Cache: miss` | local API |
| `03-desktop-light-cache-hit-evidence.png` | the same question again: `X-Cache: hit`, and the evidence trail opened (similarity scores, the cited passage marked) | local API |
| `04-desktop-dark-abstain-and-answer.png` | night map: an honest "Not in the documents" answer, then a grounded one | local API |
| `05-mobile-light-welcome.png` | phone size (390 × 844) | local API |
| `06-mobile-light-answer.png`, `07-mobile-dark-answer.png` | an answer on a phone, day and night | local API |
| `08-desktop-error-service-not-ready.png` | the real 503 the API returns when Chroma is stopped, with an "Ask again" button | local API, Chroma stopped on purpose |
| `09-minikube-ingress-answer.png` | a real answer through the **minikube Ingress** (`Host: rahbar.local`), with the page served by the `ui` nginx pod and its CSP header | live minikube cluster |

## How they were taken

- **"local API":** the real API ran on the laptop
  (`uvicorn app.main:app` with `SERVE_UI_DIR=../ui`, `RETRIEVER=chroma`,
  `LLM_PROVIDER=ollama-host`). It used the existing real-index Chroma container and a
  throwaway Redis container.
- **The screenshots:** a headless Microsoft Edge was driven over the DevTools protocol by a
  small Node script. It typed each question into the page, submitted it, waited for the real
  answer, and saved the screenshot.
- **The same script also checked:**
  - with `prefers-reduced-motion: reduce`, the mascot's animation drops to effectively 0 s,
    once; normally it is 3.2 s, repeating;
  - no console errors occurred, apart from the deliberate 503 in screenshot 08.
- **By hand, in the Claude desktop app's built-in browser:**
  - typing a question and pressing Enter;
  - Shift+Enter for a new line;
  - Tab to the suggestions;
  - the 3-character minimum;
  - HTML inside a question (`<img src=x onerror=...>`), which is shown as text: no element
    is created;
  - a stopped API ("Can't reach RAHBAR"), and recovery with "Ask again".
