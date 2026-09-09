import { useEffect, useState, useSyncExternalStore } from 'react'
import { isResearchDocument, readRoute, type ResearchDocument } from './model'

const STORAGE_KEY = 'minialpha.workspace.v1'
const subscribe = (onChange: () => void) => {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}
const snapshot = () => window.location.hash

export function useWorkspace() {
  const hash = useSyncExternalStore(subscribe, snapshot)
  const [documents, setDocuments] = useState<ResearchDocument[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '[]')
      return Array.isArray(saved) ? saved.filter(isResearchDocument).slice(0, 40) : []
    } catch {
      return []
    }
  })
  const [storageError, setStorageError] = useState<string | null>(null)
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(documents))
      setStorageError(null)
    } catch {
      setStorageError(
        'Workspace shortcuts could not be saved on this device. Research is still available in your conversation history.',
      )
    }
  }, [documents])
  const saveDocument = (document: ResearchDocument) => {
    setDocuments((current) =>
      [document, ...current.filter((entry) => entry.id !== document.id)].slice(0, 40),
    )
  }
  return { route: readRoute(hash), documents, saveDocument, storageError }
}
