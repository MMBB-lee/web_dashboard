<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId } from 'vue'

type Option = { value: string; label: string }

const props = defineProps<{
  modelValue: string
  options: Option[]
  label: string
  searchable?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const searchInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const search = ref('')
const listId = useId()
const filteredOptions = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  return query
    ? props.options.filter(option => option.label.toLocaleLowerCase().includes(query))
    : props.options
})
const selectedLabel = computed(() =>
  props.options.find(option => option.value === props.modelValue)?.label ?? '请选择',
)

function focusOption(index: number) {
  const buttons = root.value?.querySelectorAll<HTMLButtonElement>('[role="option"]')
  buttons?.[Math.max(0, Math.min(index, buttons.length - 1))]?.focus()
}

async function showOptions(index = props.options.findIndex(option => option.value === props.modelValue)) {
  search.value = ''
  open.value = true
  await nextTick()
  if (props.searchable) searchInput.value?.focus()
  else focusOption(index < 0 ? 0 : index)
}

function closeOptions(restoreFocus = false) {
  open.value = false
  if (restoreFocus) void nextTick(() => trigger.value?.focus())
}

function choose(value: string) {
  emit('update:modelValue', value)
  closeOptions(true)
}

function onTriggerKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    void showOptions(event.key === 'ArrowUp' ? props.options.length - 1 : 0)
  }
}

function onOptionKeydown(event: KeyboardEvent, index: number) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (props.searchable && event.key === 'ArrowUp' && index === 0) searchInput.value?.focus()
    else focusOption(index + (event.key === 'ArrowDown' ? 1 : -1))
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeOptions(true)
  }
}

function onSearchKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    focusOption(event.key === 'ArrowDown' ? 0 : filteredOptions.value.length - 1)
  } else if (event.key === 'Enter' && filteredOptions.value.length) {
    event.preventDefault()
    choose(filteredOptions.value[0].value)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    closeOptions(true)
  }
}

function onDocumentPointerDown(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) closeOptions()
}

function onFocusOut(event: FocusEvent) {
  if (root.value && !root.value.contains(event.relatedTarget as Node)) closeOptions()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onUnmounted(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<template>
  <div ref="root" class="ui-select" :class="{ 'is-open': open }" @focusout="onFocusOut">
    <button
      ref="trigger"
      type="button"
      class="ui-select-trigger"
      :aria-label="label"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="listId"
      @click="open ? closeOptions() : showOptions()"
      @keydown="onTriggerKeydown"
    >
      <span>{{ selectedLabel }}</span><span class="ui-select-chevron" aria-hidden="true" />
    </button>
    <Transition name="select-fade">
      <div v-if="open" class="ui-select-menu">
        <div v-if="searchable" class="ui-select-search-wrap">
          <input
            ref="searchInput"
            v-model="search"
            class="ui-select-search"
            type="search"
            :aria-label="`搜索${label}`"
            placeholder="搜索景区"
            autocomplete="off"
            @keydown="onSearchKeydown"
          />
        </div>
        <div :id="listId" role="listbox" :aria-label="label">
          <button
            v-for="(option, index) in filteredOptions"
            :key="option.value"
            type="button"
            class="ui-select-option"
            role="option"
            :aria-selected="option.value === modelValue"
            @click="choose(option.value)"
            @keydown="onOptionKeydown($event, index)"
          >{{ option.label }}</button>
          <div v-if="searchable && !filteredOptions.length" class="ui-select-empty" role="status">未找到匹配景区</div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.ui-select { position: relative; min-width: var(--select-min-width, 108px); }
.ui-select.is-open { z-index: 30; }
.ui-select-trigger { display: flex; align-items: center; justify-content: space-between; gap: 14px; width: 100%; height: var(--select-height, 31px); padding: 0 10px; border: 1px solid var(--border-glow); border-radius: 0; background: #10274b; color: var(--text-title); text-align: left; white-space: nowrap; }
.ui-select-trigger:hover, .ui-select.is-open .ui-select-trigger { border-color: rgba(0, 212, 255, .7); }
.ui-select-chevron { width: 8px; height: 8px; flex: none; border-right: 2px solid currentColor; border-bottom: 2px solid currentColor; transform: translateY(-2px) rotate(45deg); }
.ui-select-menu { position: absolute; top: calc(100% + 3px); left: 0; width: 100%; max-height: 220px; overflow-y: auto; padding: 3px; border: 1px solid var(--border-glow); border-radius: 0; background: #10274b; box-shadow: 0 12px 22px rgba(0, 7, 20, .35); }
.ui-select-search-wrap { padding: 3px; }
.ui-select-search { width: 100%; height: 30px; min-width: 0; padding: 0 8px; border: 1px solid var(--border-glow); border-radius: 0; outline: none; background: #091d39; color: var(--text-title); font: inherit; }
.ui-select-search:focus { border-color: var(--accent); }
.ui-select-search::placeholder { color: var(--text-sub); }
.ui-select-option { display: block; width: 100%; min-height: 31px; padding: 6px 9px; border: 0; border-radius: 0; background: transparent; color: var(--text-title); text-align: left; white-space: nowrap; }
.ui-select-empty { padding: 8px 9px; color: var(--text-sub); white-space: nowrap; }
.ui-select-option:hover, .ui-select-option:focus-visible { background: rgba(0, 212, 255, .16); }
.ui-select-option[aria-selected="true"] { background: rgba(0, 212, 255, .2); color: #e6f7ff; }
.select-fade-enter-active, .select-fade-leave-active { transition: opacity .16s ease, transform .16s ease; }
.select-fade-enter-from, .select-fade-leave-to { opacity: 0; transform: translateY(-5px); }
@media (prefers-reduced-motion: reduce) { .select-fade-enter-active, .select-fade-leave-active { transition: none; } }
</style>
