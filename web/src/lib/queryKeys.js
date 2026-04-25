// TanStack Query keys and mutation keys per plan §4.5.
// Keep in sync with backend endpoints (see plan §4.2 HTTP API).

export const qk = {
  systemInfo: () => ['system', 'info'],
  settings: () => ['settings'],
  documents: () => ['documents'],
  document: (id) => ['documents', id],
  transcript: (id) => ['documents', id, 'transcript'],
  summary: (id) => ['documents', id, 'summary'],
  glossary: () => ['glossary'],
  prompts: () => ['prompts'],
  prompt: (id) => ['prompts', id],
}

export const mk = {
  createDocument: 'createDocument',
  deleteDocument: 'deleteDocument',
  updateDocument: 'updateDocument',
  saveGlossary: 'saveGlossary',
  downloadModel: 'downloadModel',
  startRecording: 'startRecording',
  finalizeRecording: 'finalizeRecording',
  cancelJob: 'cancelJob',
  createPrompt: 'createPrompt',
  updatePrompt: 'updatePrompt',
  deletePrompt: 'deletePrompt',
  reorderPrompts: 'reorderPrompts',
}
