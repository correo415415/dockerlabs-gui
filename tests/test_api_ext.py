"""Tests del parser de máquinas completadas (html.parser) de dockerlabs_api_ext."""
from __future__ import annotations

from dockerlabs_api_ext import parse_completed_machines

SAMPLE_HOME = """
<html><body>
<div class="lista">
  <div onclick="presentacion(&#34;Asturias&#34;, &#34;Medio&#34;, &#34;#e0a553&#34;, &#34;autor&#34;)"
       class="maquina-item medio completada">
    <span>Asturias</span>
  </div>
  <div onclick="presentacion(&#34;Trust&#34;, &#34;Muy Fácil&#34;, &#34;#2ecc71&#34;, &#34;autor&#34;)"
       class="maquina-item muyfacil">
    <span>Trust</span>
  </div>
  <div class="maquina-item dificil completada"
       onclick="presentacion(&#34;Grandma&#34;, &#34;Difícil&#34;, &#34;#c0392b&#34;, &#34;autor&#34;)">
    <span>Grandma</span>
  </div>
  <div onclick="presentacion(&#34;WhereIsMyWebShell&#34;, &#34;Fácil&#34;)" class="maquina-item completada facil">
  </div>
  <div onclick="presentacion(&#34;Asturias&#34;, &#34;Medio&#34;)" class="maquina-item completada">dup</div>
  <div class="completada">no es maquina-item</div>
  <div onclick="presentacion(&#34;Sinclase&#34;, &#34;Medio&#34;)">sin class</div>
</div>
</body></html>
"""


def test_parse_completed_only_completed_items():
    names = parse_completed_machines(SAMPLE_HOME)
    assert names == ["Asturias", "Grandma", "WhereIsMyWebShell"]


def test_parse_completed_attribute_order_independent():
    html = '<div class="maquina-item completada" onclick="presentacion(&#34;Foo&#34;, &#34;x&#34;)"></div>'
    assert parse_completed_machines(html) == ["Foo"]


def test_parse_completed_single_quotes_and_unicode():
    html = "<div class='maquina-item completada' onclick=\"presentacion('Pequeñas-Mentirosas', 'Fácil')\"></div>"
    assert parse_completed_machines(html) == ["Pequeñas-Mentirosas"]


def test_parse_completed_empty_and_broken_html():
    assert parse_completed_machines("") == []
    assert parse_completed_machines("<div class='maquina-item completada' onclick=") == []
    assert parse_completed_machines("<div class='maquina-item completada'>sin onclick</div>") == []
