import {defineComponent} from 'vue'
import type {PropType} from 'vue'
import type {LinkedResult} from '../../types/domain'

const pct=(v:number)=>`${(v*100).toFixed(1)}%`
const sign=(v:number)=>v>0?`+${v}`:`${v}`

export default defineComponent({
  props:{result:{type:Object as PropType<LinkedResult>,required:true}},
  setup(props){
    const pctCell=(v:number)=>`${(v*100).toFixed(1)}%`
    const deltaClass=(v:number)=>v>0?'up':v<0?'down':'flat'
    return {pct,sign,deltaClass,pctCell}
  },
  template:`<div class="linked-result">
    <div class="metric-row">
      <p><span>总量递归回测 MAE</span><strong>{{result.total_mae}}<small>人</small></strong></p>
      <p><span>上周同日基线 MAE</span><strong>{{result.total_baseline_mae}}<small>人</small></strong></p>
      <p><span>总量区间半径（合计残差）</span><strong>±{{result.interval_radius}}<small>人</small></strong></p>
      <p><span>实际覆盖率（协调后）</span><strong>{{pct(result.realized_coverage)}}<small>名义 {{pct(result.nominal_coverage)}}</small></strong></p>
    </div>

    <div class="section-line"><h2>协调依据 / 回测残差协方差</h2><span>仅使用校准起点之前数据 · 秩亏共线时标量归一化仍可解</span></div>
    <div class="table-scroll"><table>
      <thead><tr><th>场馆</th><th>协调份额</th><th>残差 σ</th><th>回测 MAE</th><th>选用 α</th></tr></thead>
      <tbody>
        <tr v-for="vf in result.venue_forecasts" :key="vf.venue">
          <td>{{vf.venue}}</td>
          <td>{{pctCell(vf.share)}}</td>
          <td>{{vf.residual_sigma}}</td>
          <td>{{vf.mae}}</td>
          <td>{{vf.alpha}}</td>
        </tr>
      </tbody>
    </table></div>
    <p class="note-small">
      份额来自各厅与全剧场残差的协方差：同日同向涨跌越多的厅承担越多差额；负份额代表历史上与总量反向（对冲）。
      总量残差 σ = {{result.total_sigma}} 人；若把各厅区间上下界直接相加，对应 σ = {{result.naive_sum_sigma}} 人，会夸大同日合计风险。
    </p>

    <div class="section-line"><h2>未来逐日协调结果</h2><span>各厅非负且不超容量 · 合计逐日可对账</span></div>
    <div class="table-scroll"><table>
      <thead><tr>
        <th>日期</th><th>总量（独立预测）</th><th>总量（协调后）</th><th>差额</th>
        <th>总量90%区间</th>
        <th v-for="v in result.venues" :key="v">{{v}} 协调前 → 后（差额）</th>
      </tr></thead>
      <tbody>
        <tr v-for="d in result.forecast" :key="d.date">
          <td>{{d.date}}<small v-if="d.event"> 特别场</small></td>
          <td>{{d.total_raw}}</td>
          <td><b>{{d.total_prediction}}</b></td>
          <td :class="deltaClass(d.total_prediction-d.total_raw)">{{sign(Math.round((d.total_prediction-d.total_raw)*10)/10)}}</td>
          <td>{{d.total_lower}} — {{d.total_upper}}</td>
          <td v-for="v in result.venues" :key="v">
            {{d.venue_raw[v]}} → <b>{{d.venue_prediction[v]}}</b>
            <em :class="deltaClass(d.venue_delta[v])">（{{sign(d.venue_delta[v])}}）</em>
          </td>
        </tr>
      </tbody>
    </table></div>

    <div class="section-line"><h2>各厅协调后人力与容量</h2><span>非负 / 不超容量约束投影</span></div>
    <div class="table-scroll"><table>
      <thead><tr><th>场馆</th><th>容量</th><th v-for="d in result.forecast" :key="d.date">{{d.date.slice(5)}}</th></tr></thead>
      <tbody>
        <tr v-for="vf in result.venue_forecasts" :key="vf.venue">
          <td>{{vf.venue}}</td><td>{{vf.capacity}}</td>
          <td v-for="day in vf.forecast" :key="day.date">{{day.prediction}}<small> / {{day.ushers}}人</small></td>
        </tr>
      </tbody>
    </table></div>

    <div class="section-line"><h2>回测对账 / 最后14天</h2><span>实际覆盖率 {{pct(result.realized_coverage)}} · 协调前 {{pct(result.raw_coverage)}}</span></div>
    <div class="table-scroll"><table>
      <thead><tr>
        <th>日期</th><th>总量实际</th><th>独立预测</th><th>协调后</th><th>总量90%区间</th><th>命中</th>
        <th v-for="v in result.venues" :key="v">{{v}} 前→后（差）</th>
      </tr></thead>
      <tbody>
        <tr v-for="b in result.backtest" :key="b.date">
          <td>{{b.date}}</td>
          <td><b>{{b.total_actual}}</b></td>
          <td>{{b.total_raw}}</td>
          <td>{{b.total_prediction}}</td>
          <td>{{b.total_lower}} — {{b.total_upper}}</td>
          <td><span class="tag" :class="b.total_actual>=b.total_lower&&b.total_actual<=b.total_upper?'hit':'miss'">
            {{b.total_actual>=b.total_lower&&b.total_actual<=b.total_upper?'命中':'落空'}}</span></td>
          <td v-for="v in result.venues" :key="v">
            {{b.venue_raw[v]}}→{{b.venue_prediction[v]}}<em :class="deltaClass(b.venue_delta[v])">（{{sign(b.venue_delta[v])}}）</em>
          </td>
        </tr>
      </tbody>
    </table></div>

    <div class="caution"><b>阅读边界</b><p v-for="line in result.limitations" :key="line">{{line}}</p></div>
  </div>`
})
