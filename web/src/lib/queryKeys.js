// TanStack Query keys and mutation keys per plan §4.5.
// Keep in sync with backend endpoints (see plan §4.2 HTTP API).

export const qk = {
  systemInfo: () => ['system', 'info'],
  settings: () => ['settings'],
  notes: () => ['notes'],
  note: (id) => ['notes', id],
  transcript: (id) => ['notes', id, 'transcript'],
  summary: (id) => ['notes', id, 'summary'],
  glossary: () => ['glossary'],
  prompts: () => ['prompts'],
  prompt: (id) => ['prompts', id],
}

export const mk = {
  createNote: 'createNote',
  deleteNote: 'deleteNote',
  updateNote: 'updateNote',
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
