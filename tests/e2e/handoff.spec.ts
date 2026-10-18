import { test, expect } from '@playwright/test'
import { createDemoApi } from '../../web_interactions/dev/mock'
import { dashboardDemo } from '../../web_interactions/src/host/demo-api'
import { DEMO_SCENIC_IDS } from '../../web_dashboard/tests/demoData'
import { analysisId } from '../../web_interactions/dev/fixtures'
const scenicId=DEMO_SCENIC_IDS.jiuhua
async function api(page:import('@playwright/test').Page, options:{unavailable?:boolean}={}) {
 const fetcher=createDemoApi('normal',dashboardDemo('normal'))
 let countingStarted=false
 await page.route('**/api/v1/**',async route=>{
  const request=route.request(), url=request.url()
  if(url.endsWith('/counting')) {
   if(request.method()==='POST')countingStarted=true
   await route.fulfill({status:request.method()==='POST'?202:200,json:{data:countingStarted?{analysis_id:analysisId,status:'ready',processed_samples:720,total_samples:720,progress:100}:null,meta:{request_id:'counting'}}});return
  }
  const response=await fetcher(url,{method:request.method(),headers:request.headers(),body:request.postData() ?? undefined})
  const payload=await response.json()
  if(payload.data?.user)payload.data.user.role='admin'
  if(url.includes('/counts?')&&payload.data)Object.assign(payload.data,{count_model:'p2pnet',inference_engine:'pytorch',device:'CPU'})
  if(/\/events\/[^/?]+$/.test(url)&&payload.data)payload.data.affected_entity_ids=[scenicId]
  if(url.endsWith('/assistant/chat')&&payload.data)payload.data.used_metric_queries[0].entity_id=scenicId
  if(url.includes('/counts?') && options.unavailable) {await route.fulfill({status:409,json:{error:{code:'analysis_not_ready',message:'分析结果尚未就绪'}}});return}
  await route.fulfill({status:response.status,json:payload})
 })
}
async function login(page:import('@playwright/test').Page,path:string){
 await page.goto('http://127.0.0.1:5184'+path)
 await page.getByLabel('用户名',{exact:true}).fill('demo');await page.locator('#password').fill('demo2026')
 await page.getByRole('button',{name:'登录工作台'}).click()
 await expect(page.getByRole('navigation',{name:'主导航'})).toBeVisible()
}
test('event scenic object opens A scenic page',async({page})=>{
 await api(page);await login(page,'/events')
 await page.getByRole('button',{name:/合肥南站发送量波动提示/}).click()
 await page.getByRole('button',{name:'查看关联对象'}).click()
 await expect(page).toHaveURL(new RegExp('/scenic\\?id='+scenicId))
 await expect(page.locator('.scenic-summary')).toContainText('九华山')
})
test('assistant scenic query opens A scenic page',async({page})=>{
 await api(page);await login(page,'/assistant')
 await page.locator('textarea').fill('分析交通趋势');await page.getByRole('button',{name:'发送',exact:true}).click()
 await page.getByRole('button',{name:'查看图表'}).click()
 await expect(page.locator('.scenic-summary')).toContainText('九华山')
})
test('API video starts counting without UUID, handles 409 then predicts',async({page})=>{
 const state={unavailable:true};await api(page,state);await login(page,'/video')
 await expect(page.getByLabel('分析结果编号',{exact:true})).toHaveCount(0)
 await page.getByRole('button',{name:'开始计数',exact:true}).click();await expect(page.getByText('分析结果尚未就绪',{exact:true})).toBeVisible()
 await expect(page.getByRole('combobox',{name:'录像片段'})).toBeVisible()
 state.unavailable=false
 await page.getByRole('button',{name:'重新加载',exact:true}).click()
 await expect(page.getByRole('combobox',{name:'计数粒度'})).toBeVisible()
 await page.getByRole('button',{name:'短时预测',exact:true}).click()
 await expect(page.locator('.chart-footer')).toContainText('demo-damped-trend-simulated-v1')
 await expect(page.locator('.video-trend-panel tbody tr').filter({has:page.getByRole('cell',{name:'预测',exact:true,includeHidden:true})})).toHaveCount(6)
})
test('B transport map reuses A map and selects the requested station',async({page})=>{
 await page.goto('/transport');await page.getByRole('button',{name:'进入演示工作台'}).click()
 const map=page.locator('.traffic-map-panel .three-map-stage')
 await expect(map.locator('.three-map-detail')).toHaveText('16 个地级市 · 104 个区县')
 await expect(map.locator('.three-map-marker')).toHaveCount(5)
 await map.getByRole('button',{name:/芜湖站，铁路站/}).click()
 await expect(page).toHaveURL(/entity_id=10000000-0000-4000-8000-000000000004/)
 await expect(page.locator('.transport-grid')).toContainText('芜湖站')
 await expect(map.getByRole('button',{name:/芜湖站，铁路站/})).toHaveClass(/selected/)
 await page.screenshot({path:'test-results/transport-map-1920.png',fullPage:true})
})
