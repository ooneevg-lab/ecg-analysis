"""
XML parser for Poly-Spectrum.NET ECG format.

Parses 12-lead ECG data from XML files exported by Poly-Spectrum.NET software.
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from utils.signal_processing import CHANNEL_NAMES as LEAD_NAMES  # noqa: E402

# Mapping from Poly-Spectrum SubType to lead index
LEAD_MAP = {
    'ecgI': 0, 'ecgII': 1, 'ecgIII': 2,
    'ecgAVR': 3, 'ecgAVL': 4, 'ecgAVF': 5,
    'ecgV1': 6, 'ecgV2': 7, 'ecgV3': 8,
    'ecgV4': 9, 'ecgV5': 10, 'ecgV6': 11
}


class XMLParseError(Exception):
    """Exception raised when XML parsing fails."""
    pass


def parse_polyspectrum_xml(file_path: str) -> dict:
    """
    Parse XML file in Poly-Spectrum.NET format.

    Structure of the XML file:
    <File>
      <CurveList Count="1">
        <CurveSet Count="12">
          <Curve SubType="ecgI/ecgII/...">
            <Frequency Units="Hz">2000</Frequency>
            <Samples>40000</Samples>
            <Data Units="Volt">values separated by newlines</Data>
          </Curve>
        </CurveSet>
      </CurveList>
    </File>

    Args:
        file_path: Path to the XML file

    Returns:
        dict with keys:
            'leads': np.ndarray of shape [12, T]
            'sampling_rate': int (Hz)
            'lead_names': list of str
            'n_samples': int
            'qrs_points': list of float (QRS times in seconds, if present)

    Raises:
        XMLParseError: If the file cannot be parsed or is invalid
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise XMLParseError(f"Файл не найден: {file_path}")

    if not file_path.suffix.lower() == '.xml':
        raise XMLParseError("Файл должен иметь расширение .xml")

    try:
        tree = ET.parse(str(file_path))
        root = tree.getroot()
    except ET.ParseError as e:
        raise XMLParseError(f"Ошибка разбора XML: {e}")

    # Find CurveSet containing 12 leads
    curve_set = None
    for cs in root.iter('CurveSet'):
        count = cs.get('Count')
        if count and int(count) == 12:
            curve_set = cs
            break

    if curve_set is None:
        raise XMLParseError("Не найден CurveSet с 12 отведениями")

    # Parse QRS points if present
    qrs_points = []
    points_elem = curve_set.find('CurveSet.Points')
    if points_elem is not None:
        for point in points_elem.findall('Point'):
            if point.get('Type') == 'pointECGQRS':
                time_str = point.get('Time')
                if time_str:
                    try:
                        qrs_points.append(float(time_str))
                    except ValueError:
                        pass

    # Parse each curve
    leads_data = {}
    sampling_rate = None
    n_samples = None

    for curve in curve_set.findall('Curve'):
        subtype = curve.get('SubType')
        if subtype not in LEAD_MAP:
            continue

        lead_idx = LEAD_MAP[subtype]

        # Get sampling rate
        freq_elem = curve.find('Frequency')
        if freq_elem is not None:
            current_fs = int(float(freq_elem.text))
            if sampling_rate is None:
                sampling_rate = current_fs
            elif sampling_rate != current_fs:
                raise XMLParseError(
                    f"Разные частоты дискретизации: {sampling_rate} != {current_fs}"
                )

        # Get number of samples (declared)
        samples_elem = curve.find('Samples')
        if samples_elem is not None:
            current_samples = int(samples_elem.text)
            if n_samples is None:
                n_samples = current_samples

        # Parse data
        data_elem = curve.find('Data')
        if data_elem is None or data_elem.text is None:
            raise XMLParseError(f"Отсутствуют данные для отведения {subtype}")

        try:
            values = [float(v.strip()) for v in data_elem.text.strip().split('\n') if v.strip()]
            leads_data[lead_idx] = np.array(values, dtype=np.float32)
        except ValueError as e:
            raise XMLParseError(f"Ошибка разбора данных отведения {subtype}: {e}")

    # Verify we have all 12 leads
    if len(leads_data) != 12:
        missing = [LEAD_NAMES[i] for i in range(12) if i not in leads_data]
        raise XMLParseError(f"Отсутствуют отведения: {', '.join(missing)}")

    # Verify all leads have the same length
    lengths = [len(leads_data[i]) for i in range(12)]
    if len(set(lengths)) != 1:
        raise XMLParseError(
            f"Разная длина отведений: min={min(lengths)}, max={max(lengths)}"
        )

    actual_samples = lengths[0]

    # Stack into [12, T] array
    leads = np.stack([leads_data[i] for i in range(12)], axis=0)

    return {
        'leads': leads,
        'sampling_rate': sampling_rate or 500,
        'lead_names': LEAD_NAMES.copy(),
        'n_samples': actual_samples,
        'qrs_points': qrs_points if qrs_points else None
    }


def validate_parsed_data(parsed: dict, min_duration_sec: float = 10.0) -> tuple[bool, list[str]]:
    """
    Validate parsed ECG data.

    Args:
        parsed: Result from parse_polyspectrum_xml
        min_duration_sec: Minimum duration in seconds

    Returns:
        (is_valid, list of error messages)
    """
    errors = []
    leads = parsed['leads']
    fs = parsed['sampling_rate']
    n_samples = parsed['n_samples']

    # Check duration
    duration_sec = n_samples / fs
    if duration_sec < min_duration_sec:
        errors.append(
            f"Слишком короткая запись: {duration_sec:.1f} сек (минимум {min_duration_sec} сек)"
        )

    # Check for all-zero leads
    for i in range(12):
        if np.all(leads[i] == 0):
            errors.append(f"Отведение {LEAD_NAMES[i]} содержит только нули")

    # Check for NaN/Inf
    if np.any(np.isnan(leads)):
        nan_leads = [LEAD_NAMES[i] for i in range(12) if np.any(np.isnan(leads[i]))]
        errors.append(f"NaN значения в отведениях: {', '.join(nan_leads)}")

    if np.any(np.isinf(leads)):
        inf_leads = [LEAD_NAMES[i] for i in range(12) if np.any(np.isinf(leads[i]))]
        errors.append(f"Inf значения в отведениях: {', '.join(inf_leads)}")

    return len(errors) == 0, errors
