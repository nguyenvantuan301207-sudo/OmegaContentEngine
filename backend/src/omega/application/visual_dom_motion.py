import hashlib

from omega.application.visual_template_renderer import RenderedTemplateDocument


class VisualDomMotionError(ValueError):
    pass


class VisualDomMotionRuntime:
    def _clamp(self, val: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(val, max_val))

    def _progress(self, time: float, start: float, end: float, duration: float) -> float:
        if time >= duration:
            return 1.0

        start = min(start, duration)
        end = min(end, duration)

        if end <= start:
            return 1.0 if time >= start else 0.0

        return self._clamp((time - start) / (end - start), 0.0, 1.0)

    def _ease_out(self, p: float) -> float:
        return 1.0 - (1.0 - p) * (1.0 - p)

    def _ease_in_out(self, p: float) -> float:
        if p < 0.5:
            return 2.0 * p * p
        return 1.0 - (-2.0 * p + 2.0) ** 2 / 2.0

    def _fmt_float(self, val: float) -> str:
        s = f"{val:.4f}".rstrip("0").rstrip(".")
        return s if s else "0"

    def _inject_state_style(self, html: str, style: str) -> str:
        style_block = f'<style id="omega-dom-motion-state">\n{style}</style>'
        start_tag = '<style id="omega-dom-motion-state">'
        end_tag = "</style>"

        count = html.count(start_tag)
        if count > 1:
            raise VisualDomMotionError("Multiple omega-dom-motion-state blocks found.")

        if count == 1:
            try:
                before, rest = html.split(start_tag, 1)
                _inner, after = rest.split(end_tag, 1)
                return before + style_block + after
            except ValueError as e:
                raise VisualDomMotionError("Malformed omega-dom-motion-state block.") from e

        if count == 0:
            if "</head>" not in html:
                raise VisualDomMotionError("Document missing </head>.")
            return html.replace("</head>", style_block + "\n</head>", 1)

        return html  # Unreachable

    def render_frame(
        self,
        document: RenderedTemplateDocument,
        motion_profile: str | None,
        time_seconds: float,
        duration_seconds: float,
    ) -> RenderedTemplateDocument:
        if duration_seconds <= 0:
            raise VisualDomMotionError("Duration must be > 0.")

        time_seconds = self._clamp(time_seconds, 0.0, duration_seconds)

        if not motion_profile:
            return RenderedTemplateDocument(
                scene_index=document.scene_index,
                template_id=document.template_id,
                width=document.width,
                height=document.height,
                html=document.html,
                semantic_element_ids=document.semantic_element_ids,
                content_sha256=document.content_sha256,
            )

        if motion_profile == "title_reveal":
            style = self._profile_title_reveal(time_seconds, duration_seconds)
        elif motion_profile == "sequential_flow":
            style = self._profile_sequential_flow(time_seconds, duration_seconds, document)
        elif motion_profile == "metric_emphasis":
            style = self._profile_metric_emphasis(time_seconds, duration_seconds)
        elif motion_profile == "code_type_on":
            style = self._profile_code_type_on(time_seconds, duration_seconds)
        elif motion_profile == "image_explainer":
            style = self._profile_image_explainer(time_seconds, duration_seconds)
        else:
            raise VisualDomMotionError(f"Unknown motion profile: {motion_profile}")

        new_html = self._inject_state_style(document.html, style)
        content_sha256 = hashlib.sha256(new_html.encode("utf-8")).hexdigest()

        return RenderedTemplateDocument(
            scene_index=document.scene_index,
            template_id=document.template_id,
            width=document.width,
            height=document.height,
            html=new_html,
            semantic_element_ids=document.semantic_element_ids,
            content_sha256=content_sha256,
        )

    def _profile_title_reveal(self, t: float, d: float) -> str:
        p_accent = self._progress(t, 0.0, 0.55, d)
        p_title = self._ease_out(self._progress(t, 0.10, 0.90, d))
        p_sub = self._ease_out(self._progress(t, 0.45, 1.20, d))
        p_bg = self._progress(t, 0.0, d, d)

        style = []
        style.append(f"""
        #hero-accent {{
            opacity: {self._fmt_float(p_accent)};
            transform: scaleX({self._fmt_float(p_accent)});
            transform-origin: left center;
        }}
        """)

        trans_title = 42.0 * (1.0 - p_title)
        style.append(f"""
        #hero-title {{
            opacity: {self._fmt_float(p_title)};
            transform: translate3d(0, {self._fmt_float(trans_title)}px, 0);
        }}
        """)

        trans_sub = 18.0 * (1.0 - p_sub)
        style.append(f"""
        #hero-subtitle {{
            opacity: {self._fmt_float(p_sub)};
            transform: translate3d(0, {self._fmt_float(trans_sub)}px, 0);
        }}
        """)

        bg_scale = 1.0 + 0.05 * p_bg
        style.append(f"""
        [data-motion-role="background-decoration"] {{
            transform: scale({self._fmt_float(bg_scale)});
        }}
        """)

        return "".join(style)

    def _profile_sequential_flow(self, t: float, d: float, document: RenderedTemplateDocument) -> str:
        nodes = document.html.count('data-motion-role="flow-node"')
        style = []

        p_title = self._ease_out(self._progress(t, 0.0, 0.5, d))
        style.append(f"""
        #flow-title {{
            opacity: {self._fmt_float(p_title)};
        }}
        """)

        if nodes == 0:
            return "".join(style)

        seq_start = min(0.5, d)
        seq_dur = max(d - seq_start, 0.01)
        steps = 2 * nodes - 1
        step_dur = seq_dur / steps

        for i in range(nodes):
            node_start = seq_start + (2 * i) * step_dur
            node_end = node_start + step_dur
            p_node = self._ease_out(self._progress(t, node_start, node_end, d))

            node_scale = 0.92 + 0.08 * p_node
            node_trans = 16.0 * (1.0 - p_node)

            style.append(f"""
            [data-motion-role="flow-node"][data-motion-index="{i}"] {{
                opacity: {self._fmt_float(p_node)};
                transform: scale({self._fmt_float(node_scale)}) translate3d(0, {self._fmt_float(node_trans)}px, 0);
            }}
            """)

            if i < nodes - 1:
                edge_start = seq_start + (2 * i + 1) * step_dur
                edge_end = edge_start + step_dur
                p_edge = self._progress(t, edge_start, edge_end, d)

                style.append(f"""
                [data-motion-role="flow-edge"][data-motion-index="{i}"] {{
                    opacity: {self._fmt_float(p_edge)};
                    transform: scaleX({self._fmt_float(p_edge)});
                    transform-origin: left center;
                }}
                """)

        return "".join(style)

    def _profile_metric_emphasis(self, t: float, d: float) -> str:
        p_dec = self._progress(t, 0.0, 1.1, d)
        p_met = self._ease_out(self._progress(t, 0.15, 1.0, d))
        p_lab = self._ease_out(self._progress(t, 0.75, 1.45, d))

        style = []
        dec_scale = 0.95 + 0.05 * p_dec
        style.append(f"""
        [data-motion-role="stat-decoration"] {{
            opacity: {self._fmt_float(p_dec)};
            transform: scale({self._fmt_float(dec_scale)});
        }}
        """)

        met_scale = 0.86 + 0.14 * p_met
        met_trans = 12.0 * (1.0 - p_met)
        style.append(f"""
        #metric-value {{
            opacity: {self._fmt_float(p_met)};
            transform: scale({self._fmt_float(met_scale)}) translate3d(0, {self._fmt_float(met_trans)}px, 0);
        }}
        """)

        lab_trans = 10.0 * (1.0 - p_lab)
        style.append(f"""
        #metric-label {{
            opacity: {self._fmt_float(p_lab)};
            transform: translate3d(0, {self._fmt_float(lab_trans)}px, 0);
        }}
        """)

        return "".join(style)

    def _profile_code_type_on(self, t: float, d: float) -> str:
        p_pan = self._ease_out(self._progress(t, 0.0, 0.6, d))
        p_code = self._progress(t, 0.35, 1.8, d)

        style = []
        pan_trans = 20.0 * (1.0 - p_pan)
        style.append(f"""
        #code-panel {{
            opacity: {self._fmt_float(p_pan)};
            transform: translate3d(0, {self._fmt_float(pan_trans)}px, 0);
        }}
        """)

        clip_pct = p_code * 100.0
        style.append(f"""
        #code-content {{
            clip-path: polygon(0 0, {self._fmt_float(clip_pct)}% 0, {self._fmt_float(clip_pct)}% 100%, 0 100%);
        }}
        """)

        return "".join(style)

    def _profile_image_explainer(self, t: float, d: float) -> str:
        p_title = self._ease_out(self._progress(t, 0.1, 0.8, d))
        p_body = self._ease_out(self._progress(t, 0.4, 1.1, d))
        p_caption = self._ease_out(self._progress(t, 0.7, 1.4, d))
        p_frame = self._progress(t, 0.0, d, d)

        style = []
        style.append(f"""
        #image-title {{
            opacity: {self._fmt_float(p_title)};
            transform: translate3d(0, {self._fmt_float(10.0 * (1.0 - p_title))}px, 0);
        }}
        """)

        style.append(f"""
        #image-body {{
            opacity: {self._fmt_float(p_body)};
            transform: translate3d(0, {self._fmt_float(10.0 * (1.0 - p_body))}px, 0);
        }}
        """)

        style.append(f"""
        #image-caption {{
            opacity: {self._fmt_float(p_caption)};
            transform: translate3d(0, {self._fmt_float(10.0 * (1.0 - p_caption))}px, 0);
        }}
        """)

        frame_scale = 1.0 + 0.035 * p_frame
        style.append(f"""
        #image-frame {{
            transform: scale({self._fmt_float(frame_scale)});
        }}
        """)

        return "".join(style)
