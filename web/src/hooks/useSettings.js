import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'

export function useSettings(options = {}) {
  return useQuery({
    queryKey: qk.settings(),
    queryFn: api.getSettings,
    ...options,
  })
}

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data) => api.patchSettings(data),
    onSuccess: (updated) => {
      qc.setQueryData(qk.settings(), updated)
    },
  })
}

export default useSettings
