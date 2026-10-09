<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, useId } from 'vue'
import { addDays, isoWeek, monthEnd, weekStart } from '../../app/period'

const props = defineProps<{
  modelValue: string
  label: string
  max: string
  min?: string
  align?: 'left' | 'right'
}>()
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const root = ref<HTMLElement | null>(null)
const trigger = ref<HTMLButtonElement | null>(null)
const open = ref(false)
const popupId = useId()
const weekdays = ['一', '二', '三', '四', '五', '六', '日']
const displayedMonth = ref(monthOfWeek(props.modelValue))

function monthOfWeek(week: string): string {
  return addDays(weekStart(week), 3).slice(0, 7)
}

function shiftMonth(month: string, offset: number): string {
  const [year, number] = month.split('-').map(Number)
  return new Date(Date.UTC(year, number - 1 + offset, 1)).toISOString().slice(0, 7)
}

const minMonth = computed(() => props.min ? monthOfWeek(props.min) : '')
const maxMonth = computed(() => monthOfWeek(props.max))
const monthLabel = computed(() => {
  const [year, month] = displayedMonth.value.split('-')
  return `${year}年${Number(month)}月`
})
const selectedLabel = computed(() => {
  const [year, week] = props.modelValue.split('-W')
  return `${year} 年第 ${Number(week)} 周`
})

const weeks = computed(() => {
  const firstWeek = weekStart(isoWeek(`${displayedMonth.value}-01`))
  const lastDate = monthEnd(displayedMonth.value)
  const rows = []
  for (let start = firstWeek; start <= lastDate; start = addDays(start, 7)) {
    const value = isoWeek(start)
    rows.push({
      value,
      number: Number(value.slice(-2)),
      disabled: value < (props.min ?? '') || value > props.max,
      days: Array.from({ length: 7 }, (_, index) => {
        const date = addDays(start, index)
        return {
          date,
          number: Number(date.slice(-2)),
          outside: date.slice(0, 7) !== displayedMonth.value,
          monthStart: date.endsWith('-01'),
        }
      }),
    })
  }
  return rows
})

async function showWeeks() {
  displayedMonth.value = monthOfWeek(props.modelValue)
  open.value = true
  await nextTick()
  const selected = Array.from(root.value?.querySelectorAll<HTMLButtonElement>('.week-row') ?? [])
    .find(button => button.dataset.week === props.modelValue)
  selected?.focus()
}

function closeWeeks(restoreFocus = false) {
  open.value = false
  if (restoreFocus) void nextTick(() => trigger.value?.focus())
}

function chooseWeek(value: string) {
  emit('update:modelValue', value)
  closeWeeks(true)
}

function onPopupKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeWeeks(true)
  } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    const buttons = Array.from(root.value?.querySelectorAll<HTMLButtonElement>('.week-row:not(:disabled)') ?? [])
    const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
    if (index < 0) return
    event.preventDefault()
    buttons[Math.max(0, Math.min(buttons.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1)))]?.focus()
  }
}

function onDocumentPointerDown(event: PointerEvent) {
  if (root.value && !root.value.contains(event.target as Node)) closeWeeks()
}

function onFocusOut(event: FocusEvent) {
  if (root.value && !root.value.contains(event.relatedTarget as Node)) closeWeeks()
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onUnmounted(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<template>
  <div ref="root" class="week-picker" :class="{ 'is-open': open }" @focusout="onFocusOut">
    <button
      ref="trigger"
      type="button"
      class="week-trigger"
      :aria-label="label"
      aria-haspopup="dialog"
      :aria-expanded="open"
      :aria-controls="popupId"
      @click="open ? closeWeeks() : showWeeks()"
      @keydown.down.prevent="showWeeks()"
    >
      <span>{{ selectedLabel }}</span><span class="calendar-icon" aria-hidden="true" />
    </button>
    <Transition name="week-fade">
      <div
        v-if="open"
        :id="popupId"
        class="week-popup"
        :class="align === 'right' ? 'align-right' : 'align-left'"
        role="dialog"
        :aria-label="`${label}，选择周次`"
        @keydown="onPopupKeydown"
      >
        <div class="week-popup-head">
          <strong>{{ monthLabel }}</strong>
          <div class="month-actions">
            <button type="button" aria-label="上个月" :disabled="!!minMonth && displayedMonth <= minMonth" @click="displayedMonth = shiftMonth(displayedMonth, -1)">‹</button>
            <button type="button" aria-label="下个月" :disabled="displayedMonth >= maxMonth" @click="displayedMonth = shiftMonth(displayedMonth, 1)">›</button>
          </div>
        </div>
        <div class="week-grid-head"><span>周</span><span v-for="day in weekdays" :key="day">{{ day }}</span></div>
        <div class="week-grid-body">
          <button
            v-for="week in weeks"
            :key="week.value"
            type="button"
            class="week-row"
            :class="{ 'is-selected': week.value === modelValue }"
            :data-week="week.value"
            :disabled="week.disabled"
            :aria-label="`${week.value.slice(0, 4)} 年第 ${week.number} 周`"
            :aria-pressed="week.value === modelValue"
            @click="chooseWeek(week.value)"
          >
            <span class="week-number">{{ week.number }}</span>
            <span
              v-for="day in week.days"
              :key="day.date"
              class="week-day"
              :class="{ 'outside-month': day.outside, 'month-start': day.monthStart }"
            >{{ day.number }}</span>
          </button>
        </div>
        <div class="week-popup-foot"><button type="button" :disabled="max < (min ?? '')" @click="chooseWeek(max)">本周</button></div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.week-picker { position: relative; min-width: 164px; }
.week-picker.is-open { z-index: 40; }
.week-trigger { display: flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; height: var(--select-height, 31px); padding: 0 10px; border: 1px solid var(--border-glow); border-radius: 0; background: #10274b; color: var(--text-title); text-align: left; white-space: nowrap; }
.week-trigger:hover, .week-picker.is-open .week-trigger { border-color: rgba(0, 212, 255, .7); }
.calendar-icon { position: relative; width: 12px; height: 11px; flex: none; border: 1.5px solid #cbe5f7; border-top-width: 3px; }
.calendar-icon::before { content: ''; position: absolute; top: -5px; left: 2px; right: 2px; height: 2px; border-left: 1.5px solid #cbe5f7; border-right: 1.5px solid #cbe5f7; }
.week-popup { --week-divider: rgba(179, 191, 205, .43); position: absolute; top: calc(100% + 3px); width: min(348px, calc(100vw - 24px)); padding: 11px 12px 8px; border: 1px solid var(--border-glow); border-radius: 0; background: #253346; color: #f2f7ff; box-shadow: 0 16px 30px rgba(0, 6, 18, .45); }
.week-popup.align-left { left: 0; }.week-popup.align-right { right: 0; }
.week-popup-head { display: flex; align-items: center; justify-content: space-between; min-height: 31px; padding: 0 3px 8px; }
.week-popup-head strong { font-size: 13px; }
.month-actions { display: flex; gap: 4px; }
.month-actions button { width: 26px; height: 25px; border: 0; border-radius: 0; background: transparent; color: #e6f7ff; font-size: 22px; line-height: 1; }
.month-actions button:hover:not(:disabled) { background: rgba(255, 255, 255, .12); }
.month-actions button:disabled { opacity: .3; cursor: default; }
.week-grid-head, .week-row { display: grid; grid-template-columns: 40px repeat(7, minmax(0, 1fr)); align-items: stretch; }
.week-grid-head { height: 29px; border-bottom: 1px solid var(--week-divider); text-align: center; }
.week-grid-head span { display: grid; place-items: center; font-size: 12px; font-weight: 650; }
.week-grid-head span:first-child { border-right: 1px solid var(--week-divider); }
.week-grid-body { max-height: 245px; overflow-y: auto; }
.week-row { width: 100%; min-height: 36px; padding: 0; border: 0; border-radius: 0; background: transparent; color: #f2f7ff; text-align: center; }
.week-row:not(:disabled):hover, .week-row:not(:disabled):focus-visible { background: rgba(119, 181, 241, .28); }
.week-row.is-selected { background: rgba(119, 181, 241, .65); }
.week-row:disabled { color: #8795a8; cursor: default; }
.week-number, .week-day { display: grid; place-items: center; position: relative; font-size: 12px; }
.week-number { border-right: 1px solid var(--week-divider); border-bottom: 1px solid var(--week-divider); font-weight: 600; }
.week-day.outside-month { color: #a0adbd; }
.week-day.month-start::before { content: ''; position: absolute; top: 4px; bottom: 4px; left: 0; width: 1px; background: var(--week-divider); }
.week-popup-foot { display: flex; justify-content: flex-end; padding-top: 7px; border-top: 1px solid var(--week-divider); }
.week-popup-foot button { padding: 3px 5px; border: 0; border-radius: 0; background: transparent; color: #9ccfff; font-size: 12px; }
.week-popup-foot button:hover:not(:disabled) { color: #e6f7ff; }
.week-popup-foot button:disabled { opacity: .35; cursor: default; }
.week-fade-enter-active, .week-fade-leave-active { transition: opacity .16s ease, transform .16s ease; }
.week-fade-enter-from, .week-fade-leave-to { opacity: 0; transform: translateY(-5px); }
@media (prefers-reduced-motion: reduce) { .week-fade-enter-active, .week-fade-leave-active { transition: none; } }
</style>
