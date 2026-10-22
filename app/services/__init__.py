"""Lógica de negocio compartida, separada de los routers (pantallas).

Acá va lo que hoy está repetido en varios routers. La regla: si una cuenta o
una decisión del negocio se usa en más de una pantalla, vive acá y los
routers la llaman. Así se corrige en un solo lugar y se puede testear sin
levantar la app.
"""
