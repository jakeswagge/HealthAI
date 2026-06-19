"""Minimal Vertex AI Gemini chat app using google-genai + Streamlit.

This is intentionally separate from the HealthAI workflow. It is a quick ADC
smoke test for Vertex AI billing/routing and keeps chat history in
``st.session_state`` using ``client.chats.create``.
"""

from __future__ import annotations

import os

os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"
os.environ["GOOGLE_CLOUD_PROJECT"] = os.environ.get(
    "GOOGLE_CLOUD_PROJECT",
    "skilled-loader-468413-j6",
)
os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get(
    "GOOGLE_CLOUD_LOCATION",
    "global",
)

import streamlit as st
from google import genai


MODEL = os.environ.get("HEALTHAI_GEMINI_MODEL", "gemini-3.5-flash")


def _client():
    if "vertex_genai_client" not in st.session_state:
        st.session_state.vertex_genai_client = genai.Client()
    return st.session_state.vertex_genai_client


def _chat():
    if "vertex_gemini_chat" not in st.session_state:
        st.session_state.vertex_gemini_chat = _client().chats.create(model=MODEL)
    return st.session_state.vertex_gemini_chat


st.set_page_config(page_title="Vertex Gemini Chat", page_icon="G")
st.title("Vertex AI Gemini Chat")
st.caption(f"Project: {os.environ['GOOGLE_CLOUD_PROJECT']} | Model: {MODEL}")

if "vertex_messages" not in st.session_state:
    st.session_state.vertex_messages = []

for message in st.session_state.vertex_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

prompt = st.chat_input("Ask Vertex Gemini...")
if prompt:
    st.session_state.vertex_messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Calling Vertex AI..."):
            response = _chat().send_message(prompt)
            text = getattr(response, "text", "") or ""
            st.markdown(text)
    st.session_state.vertex_messages.append({"role": "assistant", "content": text})
