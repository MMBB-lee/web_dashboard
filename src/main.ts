import { createApp } from 'vue'
import App from './App.vue'
import { router } from './app/router'
import { installRouteGuard } from './app/route_guard'
import './styles/tokens.css'
import './styles/global.css'

installRouteGuard(router)
createApp(App).use(router).mount('#app')
