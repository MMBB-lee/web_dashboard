<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'

const props = withDefaults(defineProps<{ text: string; label: string; align?: 'start' | 'end' }>(), { align: 'end' })
const details = ref<HTMLDetailsElement | null>(null)
function closeOutside(event: PointerEvent) {
  if (details.value && !details.value.contains(event.target as Node)) details.value.open = false
}
function closeOnEscape(event: KeyboardEvent) {
  if (event.key === 'Escape' && details.value?.open) {
    details.value.open = false
    details.value.querySelector('summary')?.focus()
  }
}
watch(() => props.text, () => { if (details.value) details.value.open = false })
onMounted(() => {
  document.addEventListener('pointerdown', closeOutside)
  document.addEventListener('keydown', closeOnEscape)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', closeOutside)
  document.removeEventListener('keydown', closeOnEscape)
})
</script>

<template>
  <details ref="details" class="text-disclosure" :class="`align-${align}`">
    <summary :aria-label="label" :title="label">
      <span class="disclosure-preview">{{ text }}</span><span class="disclosure-arrow" aria-hidden="true" />
    </summary>
    <div class="disclosure-content"><slot>{{ text }}</slot></div>
  </details>
</template>

<style scoped>
.text-disclosure { position: relative; min-width: 0; width: 100%; }
.text-disclosure[open] { z-index: 30; }
summary { display: flex; align-items: center; gap: 10px; min-width: 0; cursor: pointer; list-style: none; line-height: 1.6; }
summary::-webkit-details-marker { display: none; }
summary:focus-visible { outline: 1px solid var(--accent); outline-offset: 3px; }
.disclosure-preview { flex: 1; min-width: 0; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.disclosure-arrow { flex: none; display: grid; place-items: center; width: 25px; height: 22px; border: 1px solid var(--border-glow); background: rgba(0, 212, 255, .06); color: var(--accent); }
.disclosure-arrow::after { content: ''; width: 6px; height: 6px; border-right: 1.5px solid currentColor; border-bottom: 1.5px solid currentColor; transform: translateY(-2px) rotate(45deg); }
[open] .disclosure-arrow::after { transform: translateY(2px) rotate(225deg); }
.disclosure-content { position: absolute; bottom: calc(100% + 9px); right: 0; box-sizing: border-box; width: min(520px, calc(100vw - 40px)); max-height: min(360px, 60vh); overflow: auto; padding: 14px 16px; border: 1px solid var(--border-glow); background: #0c2343; box-shadow: 0 8px 28px rgba(0, 0, 0, .4); color: var(--text-main); font-size: 12px; line-height: 1.75; white-space: normal; overflow-wrap: anywhere; }
.align-start .disclosure-content { left: 0; right: auto; }
</style>
