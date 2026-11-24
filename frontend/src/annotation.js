// Annotation coordinates stay relative to the original screenshot, including after cropping.
export function attachCanvas(canvas, sourceImage, shot, onToolChange) {
  let crop = shot.crop ?? null, marks = [...shot.marks], undoStack = [];
  let pointer = null, draft = null, tool = "rect";

  function fullFrame() {
    return {x: 0, y: 0, width: sourceImage.naturalWidth, height: sourceImage.naturalHeight};
  }

  function visibleFrame() {
    return tool === "crop" || !crop ? fullFrame() : crop;
  }

  function drawMark(ctx, mark) {
    const w = sourceImage.naturalWidth, h = sourceImage.naturalHeight;
    const x1 = mark.x1 * w, x2 = mark.x2 * w, y1 = mark.y1 * h, y2 = mark.y2 * h;
    const thickness = Math.max(2, Math.min(w, h) / 200);
    ctx.strokeStyle = "#ed3639"; ctx.fillStyle = "#ed3639"; ctx.lineWidth = thickness;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    if (mark.type === "rect") {
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
      return;
    }
    const angle = Math.atan2(y2 - y1, x2 - x1);
    const head = Math.min(Math.hypot(x2-x1, y2-y1) * .6, thickness * 4.3);
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - head * Math.cos(angle - .48), y2 - head * Math.sin(angle - .48));
    ctx.lineTo(x2 - head * Math.cos(angle + .48), y2 - head * Math.sin(angle + .48));
    ctx.closePath(); ctx.fill();
  }

  // Mark coordinates always refer to the original. The canvas clips strokes at the crop edges.
  function renderImage(surface, frame, pendingMark) {
    if (surface.width !== frame.width || surface.height !== frame.height) {
      surface.width = frame.width;
      surface.height = frame.height;
      if (surface === canvas) surface.style.setProperty("--image-ratio", frame.width / frame.height);
    }
    const ctx = surface.getContext("2d");
    ctx.clearRect(0, 0, surface.width, surface.height);
    ctx.drawImage(sourceImage, frame.x, frame.y, frame.width, frame.height, 0, 0, frame.width, frame.height);
    ctx.save();
    ctx.translate(-frame.x, -frame.y);
    for (const mark of marks) drawMark(ctx, mark);
    if (pendingMark) drawMark(ctx, pendingMark);
    ctx.restore();
  }

  function selectedPixels(selection) {
    const w = sourceImage.naturalWidth, h = sourceImage.naturalHeight;
    const x1 = Math.round(selection.x1*w), x2 = Math.round(selection.x2*w);
    const y1 = Math.round(selection.y1*h), y2 = Math.round(selection.y2*h);
    return {x: Math.min(x1,x2), y: Math.min(y1,y2), width: Math.abs(x2-x1), height: Math.abs(y2-y1)};
  }

  function paint() {
    if (!sourceImage) return;
    renderImage(canvas, visibleFrame(), tool === "crop" ? null : draft);
    if (tool !== "crop") return;
    const rect = draft ? selectedPixels(draft) : crop;
    if (!rect) return;
    const ctx = canvas.getContext("2d");
    const scale = canvas.width / (canvas.clientWidth || canvas.width);
    ctx.save();
    ctx.fillStyle = "rgba(47,47,44,.56)";
    ctx.beginPath(); ctx.rect(0, 0, canvas.width, canvas.height);
    ctx.rect(rect.x, rect.y, rect.width, rect.height); ctx.fill("evenodd");
    ctx.lineWidth = 2 * scale; ctx.strokeStyle = "#fff";
    ctx.setLineDash([5 * scale, 4 * scale]); ctx.strokeRect(rect.x, rect.y, rect.width, rect.height);
    ctx.restore();
  }

  function point(event) {
    const box = canvas.getBoundingClientRect(), frame = visibleFrame();
    const bound = n => Math.max(0, Math.min(1, n));
    return [(frame.x + bound((event.clientX-box.left)/box.width)*frame.width) / sourceImage.naturalWidth,
            (frame.y + bound((event.clientY-box.top)/box.height)*frame.height) / sourceImage.naturalHeight];
  }

  function endDrag() {
    const id = pointer;
    pointer = null; draft = null;
    if (id !== null && canvas.hasPointerCapture(id)) canvas.releasePointerCapture(id);
  }

  function setTool(next) {
    endDrag();
    tool = next;
    canvas.dataset.tool = next;
    onToolChange(next);
    paint();
  }

  function applyCrop(next) {
    if (next && next.width === sourceImage.naturalWidth && next.height === sourceImage.naturalHeight) next = null;
    if (JSON.stringify(next) !== JSON.stringify(crop)) {
      undoStack.push({marks, crop});
      crop = next;
    }
    setTool("rect");
  }

  canvas.onpointerdown = event => {
    if (!sourceImage || pointer !== null || event.button !== 0 || !["rect", "arrow", "crop"].includes(tool)) return;
    const [x, y] = point(event);
    pointer = event.pointerId;
    canvas.setPointerCapture(pointer);
    draft = {type: tool, x1: x, y1: y, x2: x, y2: y};
  };
  canvas.onpointermove = event => {
    if (event.pointerId !== pointer || !draft) return;
    [draft.x2, draft.y2] = point(event); paint();
  };
  canvas.onpointerup = event => {
    if (event.pointerId !== pointer || !draft) return;
    [draft.x2, draft.y2] = point(event);
    const mark = draft, rect = selectedPixels(mark);
    endDrag();
    if (tool === "crop") {
      if (rect.width && rect.height) { applyCrop(rect); return; }
    } else if (Math.hypot(rect.width, rect.height) >= 3) {
      undoStack.push({marks, crop});
      marks = [...marks, mark];
    }
    paint();
  };
  canvas.onpointercancel = canvas.onlostpointercapture = event => {
    if (event.pointerId === pointer) { endDrag(); paint(); }
  };
  canvas.ondblclick = () => { if (tool === "crop") applyCrop(null); };

  function undo() {
    const previous = undoStack.pop();
    if (previous) { marks = previous.marks; crop = previous.crop; }
    else if (marks.length) marks = marks.slice(0, -1);
    else crop = null;
    setTool("rect");
  }

  function changed() {
    return JSON.stringify(crop) !== JSON.stringify(shot.crop ?? null) ||
      JSON.stringify(marks) !== JSON.stringify(shot.marks);
  }

  function result() {
    const data = { crop, marks };
    if (changed() && (crop || marks.length)) {
      const output = document.createElement("canvas");
      renderImage(output, crop || fullFrame());
      data.rendered = output.toDataURL("image/png").split(",")[1];
    }
    return data;
  }

  setTool("rect");
  return {
    setTool, undo, changed, result,
    cancelDrawing() {
      if (pointer === null && tool !== "crop") return false;
      setTool("rect");
      return true;
    },
    detach() {
      endDrag();
      canvas.onpointerdown = canvas.onpointermove = canvas.onpointerup = null;
      canvas.onpointercancel = canvas.onlostpointercapture = canvas.ondblclick = null;
    },
  };
}
