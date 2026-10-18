import { test, expect, type Page } from '@playwright/test'
import { createDemoApi } from '../../web_interactions/dev/mock'
import { dashboardDemo } from '../../web_interactions/src/host/demo-api'
import { analysisId, demoCameraAnalyses, videos } from '../../web_interactions/dev/fixtures'

const modelVersion = 'demo-damped-trend-simulated-v1'
const chart = (page: Page) => page.locator('.video-trend-panel .trend-chart')
const historyRows = (page: Page) => page.locator('.video-trend-panel tbody tr').filter({ has: page.getByRole('cell', { name: '计数估计', exact: true, includeHidden: true }) })
const forecastRows = (page: Page) => page.locator('.video-trend-panel tbody tr').filter({ has: page.getByRole('cell', { name: '预测', exact: true, includeHidden: true }) })

async function enterDemo(page: Page) {
  await page.goto('/video')
  await page.getByRole('button', { name: '进入演示工作台' }).click()
  await expect(page.getByRole('combobox', { name: '计数粒度' })).toBeVisible()
  await expect(page.locator('.count-metadata')).toBeVisible()
}

async function choose(page: Page, label: string, value: string | RegExp) {
  await page.getByRole('combobox', { name: label, exact: true }).click()
  await page.getByRole('option', { name: value, exact: typeof value === 'string' }).click()
}

async function seek(page: Page, seconds: number) {
  await page.locator('#replay-position').evaluate((input: HTMLInputElement, value) => {
    input.value = String(value)
    input.dispatchEvent(new Event('input', { bubbles: true }))
  }, seconds)
}

async function predict(page: Page, points = 6) {
  await page.getByRole('button', { name: '短时预测', exact: true }).click()
  await expect(forecastRows(page)).toHaveCount(points)
  await expect(page.locator('.chart-footer')).toContainText(modelVersion)
}

async function expectCleared(page: Page) {
  await expect(chart(page)).toBeVisible()
  await expect(forecastRows(page)).toHaveCount(0)
  await expect(page.locator('.chart-footer')).not.toContainText(modelVersion)
  await expect(page.locator('.forecast-validation')).toHaveCount(0)
}

test('dense and sparse samples show latest visible people with quality and analysis metadata', async ({ page }) => {
  await enterDemo(page)
  await expect(page.locator('#replay-position')).toHaveValue('7200')
  await expect(page.getByRole('combobox', { name: '预测时长' })).toContainText('未来 30 分钟')
  await expect(page.locator('.replay-panel h2')).toContainText('密集画面模拟样本')
  await expect(page.locator('.counter-panel')).toContainText('最近窗口人数')
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText(
    `${demoCameraAnalyses[analysisId]!.analysis.points[23]!.value}人`,
  )
  await expect(page.locator('.counter-panel')).toContainText('30 帧')
  await expect(page.locator('.counter-panel')).toContainText('人工核验（模拟）')
  await expect(page.locator('.counter-panel')).toContainText('12 帧 · MAE 3.6 人')
  await expect(page.getByRole('img', { name: '固定统计区域 ROI 归一化坐标示意' })).toBeVisible()
  await expect(page.locator('.count-metadata')).toContainText('DM-Count')
  await expect(page.locator('.count-metadata')).toContainText('demo-dm-count-simulated')
  await expect(page.locator('.count-metadata')).toContainText('pytorch / 模拟数据 · 未执行推理')
  await expect(page.locator('.count-metadata')).toContainText('10 秒')
  await expect(page.locator('.video-page')).not.toContainText('累计')
  await expect(page.getByRole('combobox', { name: /方向/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /进入人数|离开人数|进入方向|离开方向/ })).toHaveCount(0)
  await expect(historyRows(page)).toHaveCount(24)
  await expect(forecastRows(page)).toHaveCount(0)
  await predict(page)
  await expect(page.locator('.forecast-validation')).toContainText('30 分钟 MAE 6.4 人')
  await expect(page.getByText('预测区间', { exact: true })).toBeVisible()

  await choose(page, '录像片段', /稀疏画面模拟样本/)
  await expect(page.locator('.replay-panel h2')).toContainText('稀疏画面模拟样本')
  await expectCleared(page)
  const sparse = demoCameraAnalyses['60000000-0000-4000-8000-000000000002']!.analysis.points[23]!
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText(`${sparse.value}人`)
  await expect(page.locator('.counter-panel')).toContainText('12 帧 · MAE 0.8 人')
  expect(sparse.value).toBeLessThan(10)
  await predict(page)
})

test('zero counts and missing counts stay distinct in both viewing grains', async ({ page }) => {
  await enterDemo(page)
  await seek(page, 300)
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText('0人')
  await expect(page.locator('.counter-panel')).toContainText('30 帧')
  await seek(page, 600)
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText('—人')
  await expect(page.locator('.counter-panel')).toContainText('缺失')
  await expect(page.locator('.counter-panel')).toContainText('0 帧')
  await page.getByText('查看图表数据', { exact: true }).click()
  const rows = page.locator('.video-trend-panel tbody tr')
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0).locator('td').nth(2)).toHaveText('0')
  await expect(rows.nth(1).locator('td').nth(2)).toHaveText('缺失')
  await expect(rows.nth(1).locator('td').nth(3)).toHaveText('人')

  await seek(page, 7200)
  await choose(page, '计数粒度', '逐帧估计')
  await expect(page.locator('.counter-panel')).toContainText('最近帧人数')
  await expect(historyRows(page)).toHaveCount(721)
  await expect(forecastRows(page)).toHaveCount(0)
  await expect(page.locator('.chart-footer')).toContainText('单位：人 · 逐帧估计')
  await seek(page, 290)
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText('0人')
  await seek(page, 300)
  await expect(page.locator('.counter-panel .count-item strong')).toHaveText('—人')
  await expect(page.locator('.counter-panel')).toContainText('缺失')
})

test('forecast is cleared when cutoff, viewing grain, horizon or video changes', async ({ page }) => {
  await enterDemo(page)
  await predict(page)
  await seek(page, 7500)
  await expectCleared(page)
  await predict(page)
  await choose(page, '计数粒度', '逐帧估计')
  await expectCleared(page)
  await predict(page)
  await choose(page, '预测时长', '未来 15 分钟')
  await expectCleared(page)
  await predict(page, 3)
  await expect(page.locator('.forecast-validation')).toContainText('15 分钟 MAE 4.2 人')
  await choose(page, '录像片段', /稀疏画面模拟样本/)
  await expectCleared(page)
})

test('insufficient history, poor quality and unsupported 60 minute backtest never plot forecasts', async ({ page }) => {
  await enterDemo(page)
  await seek(page, 3600)
  await page.getByRole('button', { name: '短时预测', exact: true }).click()
  await expect(page.getByText('模拟起步门槛：未来 30 分钟预测需要截止位置前约 120 分钟历史', { exact: true })).toBeVisible()
  await expect(forecastRows(page)).toHaveCount(0)
  await expect(page.locator('.forecast-validation')).toHaveCount(0)
  await expect(page.getByText('预测区间', { exact: true })).toHaveCount(0)

  await seek(page, 6600)
  await page.getByRole('button', { name: '短时预测', exact: true }).click()
  await expect(page.getByText('模拟质量门槛：最近两个窗口存在缺失或遮挡，暂停预测；缺失值未补零', { exact: true })).toBeVisible()
  await expect(forecastRows(page)).toHaveCount(0)
  await expect(page.locator('.counter-panel')).toContainText('遮挡')

  await seek(page, 10800)
  await choose(page, '预测时长', '未来 60 分钟')
  await page.getByRole('button', { name: '短时预测', exact: true }).click()
  await expect(page.getByText('模拟样本仅有 3 小时单日记录，缺少同摄像头多天数据及 60 分钟时间顺序回测，暂不开放该跨度', { exact: true })).toBeVisible()
  await expect(forecastRows(page)).toHaveCount(0)
  await expect(page.getByText('预测区间', { exact: true })).toHaveCount(0)
})

test('API requests use visible people metadata, CSRF and only the forecast contract fields; 409 can recover', async ({ page }) => {
  const fetcher = createDemoApi('normal', dashboardDemo('normal'))
  const requests: { path: string; params: Record<string, string>; method: string; csrf?: string; body: Record<string, unknown> | null }[] = []
  let unavailable = true
  let countingStarted = false
  let forecastStatus: 'ready' | 'data_insufficient' | 'quality_insufficient' = 'ready'
  let malformedRoi = false, frameUnavailable = false, negativeForecast = false
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url())
    const body = request.postDataJSON() as Record<string, unknown> | null
    requests.push({ path: url.pathname, params: Object.fromEntries(url.searchParams), method: request.method(), csrf: request.headers()['x-csrf-token'], body })
    if (url.pathname.endsWith('/counting')) {
      if (request.method() === 'POST') countingStarted = true
      await route.fulfill({ status: request.method() === 'POST' ? 202 : 200, json: { data: countingStarted ? { analysis_id: analysisId, status: 'ready', processed_samples: 720, total_samples: 720, progress: 100 } : null, meta: { request_id: 'counting' } } })
      return
    }
    if (url.pathname.endsWith('/counts') && unavailable) {
      await route.fulfill({ status: 409, json: { error: { code: 'analysis_not_ready', message: '测试分析结果尚未就绪' }, request_id: 'video-409' } })
      return
    }
    if (url.pathname.endsWith('/counts') && frameUnavailable && url.searchParams.get('grain') === 'frame') {
      await route.fulfill({ status: 503, json: { error: { code: 'frame_unavailable', message: '逐帧分析暂时不可用' }, request_id: 'frame-503' } })
      return
    }
    const response = await fetcher(request.url(), { method: request.method(), headers: request.headers(), body: request.postData() ?? undefined })
    const payload = await response.json()
    if (payload.data?.user) payload.data.user.role = 'admin'
    if (url.pathname.endsWith('/counts') && payload.data) {
      Object.assign(payload.data, { count_model: 'p2pnet', inference_engine: 'pytorch', device: 'CPU', weights_version: 'test-p2pnet-weights', manual_validation: { sample_count: 32, mae: 1.75 } })
      if (malformedRoi) payload.data.roi = [null, [0.5], [2, 0.5]]
    }
    if (url.pathname.endsWith('/short-forecast') && payload.data && forecastStatus !== 'ready') {
      // A non-ready response must be ignored even if a server mistakenly includes future points.
      Object.assign(payload.data, { status: forecastStatus, reason: `测试预测状态：${forecastStatus}` })
    }
    if (url.pathname.endsWith('/short-forecast') && payload.data && negativeForecast) payload.data.forecast_points[0].value = -1
    await route.fulfill({ status: response.status, json: payload })
  })
  await page.goto('http://127.0.0.1:5184/video')
  await page.getByLabel('用户名', { exact: true }).fill('demo')
  await page.locator('#password').fill('demo2026')
  await page.getByRole('button', { name: '登录工作台' }).click()
  await expect(page.getByRole('combobox', { name: '录像片段' })).toBeVisible()
  await expect(page.getByLabel('分析结果编号', { exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '开始计数', exact: true }).click()
  await expect(page.getByText('测试分析结果尚未就绪', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '录像片段' })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新加载', exact: true })).toBeVisible()
  unavailable = false
  await page.getByRole('button', { name: '重新加载', exact: true }).click()
  await expect(page.getByRole('combobox', { name: '计数粒度' })).toBeVisible()
  await expect(page.locator('.count-metadata')).toContainText('pytorch / CPU')
  await expect(page.locator('.count-metadata')).toContainText('test-p2pnet-weights')
  const startRequest = requests.find(request => request.path.endsWith('/counting') && request.method === 'POST')!
  expect(startRequest.csrf).toBe('demo-csrf')
  expect(startRequest.body).toMatchObject({ count_model: 'p2pnet', inference_engine: 'pytorch', device: 'CPU' })
  await expect(page.locator('.counter-panel')).toContainText('32 帧 · MAE 1.8 人')
  await choose(page, '计数粒度', '逐帧估计')
  await expect(historyRows(page)).toHaveCount(721)
  const countRequest = requests.filter(request => request.path.endsWith('/counts')).at(-1)!
  expect(countRequest.params).toEqual({ analysis_id: analysisId, grain: 'frame' })
  await predict(page)
  const forecastRequest = requests.filter(request => request.path.endsWith('/short-forecast')).at(-1)!
  expect(forecastRequest.path).toBe(`/api/v1/videos/${videos[0]!.id}/short-forecast`)
  expect(forecastRequest.method).toBe('POST')
  expect(forecastRequest.csrf).toBe('demo-csrf')
  expect(forecastRequest.body).toEqual({ analysis_id: analysisId, cutoff_at: '2026-09-28T11:00:00+08:00', horizon_minutes: 30, step_minutes: 5 })

  for (const status of ['data_insufficient', 'quality_insufficient'] as const) {
    forecastStatus = status
    await page.getByRole('button', { name: '短时预测', exact: true }).click()
    await expect(page.getByText(`测试预测状态：${status}`, { exact: true })).toBeVisible()
    await expect(forecastRows(page)).toHaveCount(0)
    await expect(page.getByText('预测区间', { exact: true })).toHaveCount(0)
  }

  malformedRoi = true
  await page.getByRole('button', { name: '开始计数', exact: true }).click()
  await expect(page.getByText('计数接口未提供有效的归一化 ROI 多边形', { exact: true })).toBeVisible()
  await expect(page.getByRole('combobox', { name: '计数粒度' })).toBeVisible()
  malformedRoi = false
  await choose(page, '计数粒度', '5 分钟中位数')
  await expect(historyRows(page)).toHaveCount(24)
  frameUnavailable = true
  await choose(page, '计数粒度', '逐帧估计')
  await expect(page.getByText('逐帧分析暂时不可用', { exact: true })).toBeVisible()
  await choose(page, '计数粒度', '5 分钟中位数')
  await expect(historyRows(page)).toHaveCount(24)
  forecastStatus = 'ready'
  negativeForecast = true
  await page.getByRole('button', { name: '短时预测', exact: true }).click()
  await expect(page.getByText('预测接口返回了不符合约定的未来人数', { exact: true })).toBeVisible()
  await expect(forecastRows(page)).toHaveCount(0)
  negativeForecast = false
  await predict(page)
  expect(errors).toEqual([])
})

for (const size of [{ width: 1920, height: 1080 }, { width: 390, height: 844 }]) {
  test(`video visible people layout at ${size.width}`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.setViewportSize(size)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await enterDemo(page)
    await predict(page)
    expect(await page.locator('body').evaluate(element => element.scrollWidth <= innerWidth + 2)).toBe(true)
    await page.screenshot({ path: `test-results/video-counts-${size.width}.png`, fullPage: true })
    expect(errors).toEqual([])
  })
}
