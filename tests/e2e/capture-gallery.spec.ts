import { test, expect, type Page } from '@playwright/test'
import { createDemoApi } from '../../web_interactions/dev/mock'
import { dashboardDemo } from '../../web_interactions/src/host/demo-api'
import { analysisId } from '../../web_interactions/dev/fixtures'

const firstFolder = '2026-10-19T18-50-12', secondFolder = '2026-10-19T18-50-13'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDnsAAAAASUVORK5CYII=', 'base64')

async function setup(page: Page, state: { empty: boolean; fail: boolean }) {
  const fetcher = createDemoApi('normal', dashboardDemo('normal'))
  await page.route('**/capture-test/*.png', route => route.fulfill({ contentType: 'image/png', body: png }))
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url())
    const reply = (data: unknown) => route.fulfill({ json: { data, meta: { request_id: 'capture-gallery' } } })
    if (url.pathname.endsWith('/capture-folders')) {
      if (state.fail) return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: '图片目录暂时不可用' } } })
      return reply(state.empty ? [] : [firstFolder, secondFolder].map((id, i) => ({ id, name: id, image_count: i ? 1 : 2,
        created_at: `${id.slice(0, 10)}T18:50:12+08:00`, analysis_id: i ? 'another-analysis' : analysisId, video_id: null, legacy: false })))
    }
    if (url.pathname.endsWith('/capture-images')) {
      return reply((url.searchParams.get('folder') === firstFolder ? ['frame_1', 'frame_2'] : ['another_frame'])
        .map(name => ({ name: name + '.png', byte_size: png.length, url: '/capture-test/' + name + '.png' })))
    }
    if (url.pathname.endsWith('/counting')) return reply({ analysis_id: analysisId, status: 'ready', processed_samples: 2, total_samples: 2, progress: 100 })
    const response = await fetcher(request.url(), { method: request.method(), headers: request.headers(), body: request.postData() ?? undefined })
    const payload = await response.json()
    if (url.pathname.endsWith('/counts') && payload.data) Object.assign(payload.data, { count_model: 'p2pnet', inference_engine: 'pytorch', device: 'CPU', weights_version: 'test-p2pnet' })
    return route.fulfill({ status: response.status, json: payload })
  })
  await page.goto('http://127.0.0.1:5184/video')
  await page.getByLabel('用户名', { exact: true }).fill('demo')
  await page.locator('#password').fill('demo2026')
  await page.getByRole('button', { name: '登录工作台' }).click()
  await expect(page.locator('.capture-gallery')).toBeVisible()
}

async function choose(page: Page, label: string, option: string | RegExp) {
  await page.getByRole('combobox', { name: label, exact: true }).click()
  await page.getByRole('option', { name: option, exact: typeof option === 'string' }).click()
}

test('capture folders and images can be selected, traversed and displayed on mobile', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await setup(page, { empty: false, fail: false })
  const gallery = page.locator('.capture-gallery'), image = gallery.locator('img')
  await expect(page.getByRole('combobox', { name: '检测文件夹' })).toContainText(firstFolder)
  await expect(image).toHaveAttribute('alt', '检测图片：frame_1.png')
  await expect.poll(() => image.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0)
  await gallery.getByRole('button', { name: '下一张', exact: true }).click()
  await expect(image).toHaveAttribute('alt', '检测图片：frame_2.png')
  await expect(gallery.getByRole('button', { name: '下一张', exact: true })).toBeDisabled()
  await choose(page, '检测图片', 'frame_1.png')
  await expect(image).toHaveAttribute('alt', '检测图片：frame_1.png')
  await choose(page, '检测文件夹', new RegExp(secondFolder))
  await expect(image).toHaveAttribute('alt', '检测图片：another_frame.png')
  await expect(gallery).toContainText('第 1 / 1 张')
  await page.setViewportSize({ width: 390, height: 844 })
  // Existing panel corner decorations extend one pixel across the border.
  await expect.poll(() => gallery.evaluate(el => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  expect(errors).toEqual([])
})

test('empty capture directories and read failures recover when refreshed', async ({ page }) => {
  const state = { empty: true, fail: false }
  await setup(page, state)
  const gallery = page.locator('.capture-gallery')
  await expect(gallery).toContainText('暂无检测图片')
  await expect(page.getByRole('combobox', { name: '检测文件夹' })).toHaveAttribute('aria-disabled', 'true')
  state.empty = false
  await gallery.getByRole('button', { name: '刷新图片目录' }).click()
  await expect(gallery.locator('img')).toHaveAttribute('alt', '检测图片：frame_1.png')
  state.fail = true
  await gallery.getByRole('button', { name: '刷新图片目录' }).click()
  await expect(gallery).toContainText('图片目录暂时不可用')
  await expect(gallery.locator('img')).toHaveCount(0)
  state.fail = false
  await gallery.getByRole('button', { name: '刷新图片目录' }).click()
  await expect(gallery.locator('img')).toBeVisible()
})
