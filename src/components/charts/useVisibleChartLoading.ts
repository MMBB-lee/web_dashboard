import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

export function useVisibleChartLoading(loading: Readonly<Ref<boolean>>, minimumMs = 750) {
  const visible = ref(loading.value)
  let startedAt = loading.value ? performance.now() : 0
  let timer: ReturnType<typeof setTimeout> | undefined

  watch(loading, active => {
    if (timer) clearTimeout(timer)
    timer = undefined

    if (active) {
      startedAt = performance.now()
      visible.value = true
      return
    }

    if (!visible.value) return
    const remaining = Math.max(0, minimumMs - (performance.now() - startedAt))
    if (remaining === 0) visible.value = false
    else timer = setTimeout(() => {
      visible.value = false
      timer = undefined
    }, remaining)
  }, { flush: 'sync' })

  onBeforeUnmount(() => {
    if (timer) clearTimeout(timer)
  })

  return visible
}
