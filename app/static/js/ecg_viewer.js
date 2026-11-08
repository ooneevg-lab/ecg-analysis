/**
 * ECG Viewer using Plotly.js
 * 12-lead ECG visualization with synchronized zoom/pan and saliency overlay
 */

class ECGViewer {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.ecgDataRaw = null;       // Original data (volts)
        this.ecgDataFiltered = null;
        this.ecgDataFull = null;
        this.saliencyData = null;
        this.rPeaks = null;
        this.samplingRate = 500;
        this.showSaliency = false;
        this.dataMode = 'fragment';   // 'fragment' or 'full'
        this.viewMode = 'all';        // 'all' or 'single'
        this.selectedLead = 0;
        this.leadNames = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6'];
    }

    setData(ecgDataRaw, ecgDataFiltered, samplingRate, saliencyData = null, rPeaks = null, ecgDataFull = null) {
        this.ecgDataRaw = ecgDataRaw;
        this.ecgDataFiltered = ecgDataFiltered;
        this.ecgDataFull = ecgDataFull;
        this.samplingRate = samplingRate;
        this.saliencyData = saliencyData;
        this.rPeaks = rPeaks;
    }

    getCurrentData() {
        if (this.dataMode === 'full' && this.ecgDataFull) {
            return this.ecgDataFull;
        }
        return this.ecgDataFiltered || this.ecgDataRaw;
    }

    isFullMode() {
        return this.dataMode === 'full' && this.ecgDataFull;
    }

    render() {
        const ecgData = this.getCurrentData();
        if (!ecgData) {
            console.error('No ECG data to render');
            return;
        }

        this.container.innerHTML = '';

        if (this.viewMode === 'single') {
            this.renderSingleLead(ecgData);
        } else {
            this.renderAllLeads(ecgData);
        }
    }

    renderAllLeads(ecgData) {
        const nSamples = ecgData[0].length;
        const timeAxis = Array.from({ length: nSamples }, (_, i) => i / this.samplingRate);

        const maxSamples = this.isFullMode() ? nSamples : Math.min(nSamples, this.samplingRate * 10);
        const displayTime = timeAxis.slice(0, maxSamples);

        const traces = [];

        for (let i = 0; i < 12; i++) {
            const displayData = ecgData[i].slice(0, maxSamples);

            // Scale to millivolts for display
            const displayDataMv = displayData.map(v => v * 1000);

            traces.push({
                x: displayTime,
                y: displayDataMv,
                type: maxSamples > 20000 ? 'scattergl' : 'scatter',
                mode: 'lines',
                name: this.leadNames[i],
                line: { color: '#2563eb', width: 1 },
                xaxis: i === 0 ? 'x' : `x${i + 1}`,
                yaxis: i === 0 ? 'y' : `y${i + 1}`,
                hovertemplate: '%{y:.3f} мВ<extra>' + this.leadNames[i] + '</extra>'
            });

            // Saliency overlay
            if (!this.isFullMode() && this.showSaliency && this.saliencyData) {
                this.addSaliencyTrace(traces, i, displayTime, displayDataMv, maxSamples);
            }
        }

        const layout = this.createAllLeadsLayout(displayTime);

        Plotly.newPlot(this.container, traces, layout, {
            responsive: true,
            displayModeBar: true,
            modeBarButtonsToRemove: ['lasso2d', 'select2d'],
            displaylogo: false
        });

        // ИСПРАВЛЕНО: вызов Plotly.Plots.resize() для корректного заполнения ширины при первой загрузке
        // Раньше: график не заполнял всю ширину до переключения saliency (который вызывал повторный render)
        Plotly.Plots.resize(this.container);

        this.container.classList.remove('single-lead');
    }

    renderSingleLead(ecgData) {
        const nSamples = ecgData[0].length;
        const timeAxis = Array.from({ length: nSamples }, (_, i) => i / this.samplingRate);

        const leadData = ecgData[this.selectedLead];
        const leadDataMv = leadData.map(v => v * 1000);

        const traces = [{
            x: timeAxis,
            y: leadDataMv,
            type: nSamples > 20000 ? 'scattergl' : 'scatter',
            mode: 'lines',
            name: this.leadNames[this.selectedLead],
            line: { color: '#2563eb', width: 1.5 },
            hovertemplate: '%{x:.3f} с<br>%{y:.3f} мВ<extra></extra>'
        }];

        // R-peak markers
        if (!this.isFullMode() && this.rPeaks && this.rPeaks.length > 0) {
            const rPeakTimes = this.rPeaks.map(p => p / this.samplingRate);
            const rPeakValues = this.rPeaks.map(p => {
                if (p < nSamples) return leadDataMv[p];
                return null;
            }).filter(v => v !== null);

            traces.push({
                x: rPeakTimes.slice(0, rPeakValues.length),
                y: rPeakValues,
                type: 'scatter',
                mode: 'markers',
                name: 'R-пики',
                marker: { color: '#ef4444', size: 8, symbol: 'diamond' },
                hovertemplate: 'R-пик<br>%{x:.3f} с<extra></extra>'
            });
        }

        // Saliency overlay for single lead
        if (!this.isFullMode() && this.showSaliency && this.saliencyData) {
            const saliency = this.saliencyData[this.selectedLead];
            const saliencyNorm = this.normalizeSaliency(saliency);

            // Create colored scatter for high saliency regions
            const highSaliencyX = [];
            const highSaliencyY = [];

            for (let i = 0; i < saliencyNorm.length; i++) {
                if (saliencyNorm[i] > 0.3) {
                    highSaliencyX.push(timeAxis[i]);
                    highSaliencyY.push(leadDataMv[i]);
                }
            }

            if (highSaliencyX.length > 0) {
                traces.push({
                    x: highSaliencyX,
                    y: highSaliencyY,
                    type: 'scatter',
                    mode: 'markers',
                    name: 'Высокий вклад',
                    marker: {
                        color: 'rgba(239, 68, 68, 0.6)',
                        size: 4
                    },
                    hoverinfo: 'skip'
                });
            }
        }

        const layout = {
            height: 350,
            margin: { l: 60, r: 30, t: 40, b: 50 },
            title: {
                text: `Отведение ${this.leadNames[this.selectedLead]}`,
                font: { size: 16 }
            },
            xaxis: {
                title: 'Время (с)',
                showgrid: true,
                gridcolor: '#e5e7eb'
            },
            yaxis: {
                title: 'Напряжение (мВ)',
                showgrid: true,
                gridcolor: '#e5e7eb'
            },
            showlegend: true,
            legend: { x: 1, xanchor: 'right', y: 1 }
        };

        Plotly.newPlot(this.container, traces, layout, {
            responsive: true,
            displayModeBar: true,
            displaylogo: false
        });

        // ИСПРАВЛЕНО: вызов Plotly.Plots.resize() для корректного заполнения ширины при первой загрузке
        Plotly.Plots.resize(this.container);

        this.container.classList.add('single-lead');
    }

    addSaliencyTrace(traces, leadIdx, displayTime, displayDataMv, maxSamples) {
        const saliency = this.saliencyData[leadIdx].slice(0, maxSamples);
        const saliencyNorm = this.normalizeSaliency(saliency);

        // Find high saliency regions
        const yMin = Math.min(...displayDataMv);
        const yMax = Math.max(...displayDataMv);

        // Create background coloring for high saliency
        const highIdx = [];
        for (let j = 0; j < saliencyNorm.length; j++) {
            if (saliencyNorm[j] > 0.25) {
                highIdx.push(j);
            }
        }

        if (highIdx.length > 0) {
            // Group consecutive indices
            const regions = [];
            let start = highIdx[0];
            let prev = highIdx[0];

            for (let j = 1; j < highIdx.length; j++) {
                if (highIdx[j] - prev > 5) {
                    regions.push({ start, end: prev });
                    start = highIdx[j];
                }
                prev = highIdx[j];
            }
            regions.push({ start, end: prev });

            // Add as separate traces with fill
            for (const region of regions.slice(0, 20)) { // Limit regions
                const regionTime = displayTime.slice(region.start, region.end + 1);
                const regionY = displayDataMv.slice(region.start, region.end + 1);

                traces.push({
                    x: regionTime,
                    y: regionY,
                    type: 'scatter',
                    mode: 'lines',
                    fill: 'tozeroy',
                    fillcolor: 'rgba(239, 68, 68, 0.25)',
                    line: { color: 'rgba(239, 68, 68, 0.5)', width: 2 },
                    xaxis: leadIdx === 0 ? 'x' : `x${leadIdx + 1}`,
                    yaxis: leadIdx === 0 ? 'y' : `y${leadIdx + 1}`,
                    showlegend: false,
                    hoverinfo: 'skip'
                });
            }
        }
    }

    createAllLeadsLayout(displayTime) {
        const plotHeight = 70;
        const totalHeight = plotHeight * 12 + 60;

        const layout = {
            height: totalHeight,
            showlegend: false,
            margin: { l: 55, r: 20, t: 20, b: 40 },
            grid: {
                rows: 12,
                columns: 1,
                pattern: 'independent',
                roworder: 'top to bottom'
            }
        };

        for (let i = 0; i < 12; i++) {
            const yAxisKey = i === 0 ? 'yaxis' : `yaxis${i + 1}`;
            const xAxisKey = i === 0 ? 'xaxis' : `xaxis${i + 1}`;

            layout[yAxisKey] = {
                title: { text: this.leadNames[i], font: { size: 9 }, standoff: 3 },
                tickfont: { size: 7 },
                showgrid: true,
                gridcolor: '#e5e7eb',
                zeroline: true,
                zerolinecolor: '#9ca3af'
            };

            layout[xAxisKey] = {
                showticklabels: i === 11,
                showgrid: true,
                gridcolor: '#e5e7eb',
                tickfont: { size: 8 },
                matches: i === 0 ? undefined : 'x',
                title: i === 11 ? { text: 'Время (с)', font: { size: 10 } } : undefined,
                range: [0, displayTime[displayTime.length - 1]]
            };
        }

        return layout;
    }

    normalizeSaliency(saliency) {
        const maxVal = Math.max(...saliency.map(Math.abs));
        if (maxVal === 0) return saliency.map(() => 0);
        return saliency.map(v => Math.abs(v) / maxVal);
    }

    setViewMode(mode) {
        this.viewMode = mode;
        this.render();
    }

    setSelectedLead(leadIdx) {
        this.selectedLead = leadIdx;
        if (this.viewMode === 'single') {
            this.render();
        }
    }

    toggleSaliency(show) {
        this.showSaliency = show;
        this.render();
    }

    setDataMode(mode) {
        this.dataMode = mode;
        this.render();
    }

    // Clone viewer state for fullscreen
    getState() {
        return {
            ecgDataRaw: this.ecgDataRaw,
            ecgDataFiltered: this.ecgDataFiltered,
            ecgDataFull: this.ecgDataFull,
            saliencyData: this.saliencyData,
            rPeaks: this.rPeaks,
            samplingRate: this.samplingRate,
            showSaliency: this.showSaliency,
            dataMode: this.dataMode,
            viewMode: this.viewMode,
            selectedLead: this.selectedLead,
        };
    }

    applyState(state) {
        this.ecgDataRaw = state.ecgDataRaw;
        this.ecgDataFiltered = state.ecgDataFiltered;
        this.ecgDataFull = state.ecgDataFull;
        this.saliencyData = state.saliencyData;
        this.rPeaks = state.rPeaks;
        this.samplingRate = state.samplingRate;
        this.showSaliency = state.showSaliency;
        this.dataMode = state.dataMode;
        this.viewMode = state.viewMode;
        this.selectedLead = state.selectedLead;
    }
}

// Global instance
let ecgViewer = null;

function initECGViewer(containerId) {
    ecgViewer = new ECGViewer(containerId);
    return ecgViewer;
}
