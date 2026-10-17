import * as engine from 'echarts/core'
import { GraphChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { LabelLayout } from 'echarts/features'

engine.use([GraphChart, TooltipComponent, LegendComponent, CanvasRenderer, LabelLayout])
export { engine }
